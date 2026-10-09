"""
services/ia_service.py

Camada única de acesso às IAs do VetAssist.

- Lê modelos e chaves de API da tabela `configuracoes_sistema` a CADA chamada,
  então qualquer alteração feita na tela de Configurações de IA vale na hora.
- Tenta os provedores em ordem (SLOTS_ORDEM) e passa para o próximo se um falhar
  (erro de rede, timeout, cota, JSON inválido ou resposta fora do esperado).
- Um slot só é usado se tiver modelo E chave configurados. Nenhum modelo,
  chave ou ID de IA fica fixo no código.
- Com `pesquisa_web=True`, só entram provedores com pesquisa na web: o Gemini (Pesquisa do
  Google, exige plano pago nos modelos 3.x) e modelos Groq `openai/gpt-oss-*` (ferramenta
  `browser_search` da Groq, sem chave nova). A resposta só é aceita se vier ancorada em
  fontes reais da web (devolvidas em RespostaIA.fontes).
- Se TODOS falharem, levanta IAIndisponivelError. Quem chama decide o que fazer;
  nunca devolva um valor "normal" inventado em caso de falha.
"""
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from google import genai
from google.genai import types
from openai import BadRequestError, InternalServerError, OpenAI
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("vetassist.ia")

TIMEOUT_SEGUNDOS = 20
TIMEOUT_PESQUISA_SEGUNDOS = 90  # pesquisa na web demora mais que uma resposta comum
# O endpoint da Groq é compatível com o SDK da OpenAI. Pode ser sobrescrito por env.
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")


@dataclass(frozen=True)
class Slot:
    """Descreve um 'encaixe' de IA na tela de configurações."""
    nome: str          # identificador usado pelo front: "gemini", "groq_1", "groq_2"
    tipo: str          # "gemini" ou "groq"
    chave_modelo: str  # chave do modelo em configuracoes_sistema
    chave_api: str     # chave da API em configuracoes_sistema
    envs: tuple        # variáveis de ambiente usadas como reserva para a chave


# Ordem de prioridade: Gemini (principal) -> Groq 1 -> Groq 2.
SLOTS_ORDEM = (
    Slot("gemini", "gemini", "gemini_model", "gemini_api_key",
         ("GEMINI_API_KEY_PRIMARY", "GEMINI_API_KEY")),
    Slot("groq_1", "groq", "groq_model_1", "groq_api_key_1", ("GROQ_API_KEY",)),
    Slot("groq_2", "groq", "groq_model_2", "groq_api_key_2", ("GROQ_API_KEY",)),
)

# Lista fechada de chaves aceitas pela tela de configurações (evita gravar lixo na tabela).
CAMPOS_CONFIG_IA = tuple(
    campo for slot in SLOTS_ORDEM for campo in (slot.chave_modelo, slot.chave_api)
)


def _descrever_erro(exc: Exception, limite: int = 900) -> str:
    """
    Resumo útil de um erro de provedor. Para erros do Google (ex.: 429), extrai o que
    permite diagnosticar: código/status, a MÉTRICA de cota violada, o modelo e o tempo
    de espera sugerido. Nunca inclui chaves de API.
    """
    partes = [type(exc).__name__]
    codigo, status = getattr(exc, "code", None), getattr(exc, "status", None)
    if codigo or status:
        partes.append(f"{codigo or ''} {status or ''}".strip())

    detalhes = getattr(exc, "details", None)
    lista = []
    if isinstance(detalhes, dict) and isinstance(detalhes.get("error"), dict):
        lista = detalhes["error"].get("details") or []
    violacoes, espera = [], None
    for item in lista if isinstance(lista, list) else []:
        if not isinstance(item, dict):
            continue
        for v in item.get("violations") or []:
            metrica = str(v.get("quotaMetric", "")).split("/")[-1]
            modelo = (v.get("quotaDimensions") or {}).get("model", "")
            violacoes.append(" ".join(x for x in (metrica, f"[{v.get('quotaId')}]" if v.get("quotaId") else "",
                                                  f"modelo {modelo}" if modelo else "") if x))
        espera = espera or item.get("retryDelay")
    if violacoes:
        partes.append("cota violada: " + "; ".join(violacoes[:3]))
    if espera:
        partes.append(f"tentar de novo em {espera}")
    if str(status) == "RESOURCE_EXHAUSTED" and not violacoes:
        # O Google nem sempre informa a métrica. Este é o formato visto quando um recurso
        # (ex.: pesquisa do Google/grounding) não está disponível no plano da chave.
        partes.append("sem métrica informada: cota esgotada OU recurso indisponível no plano atual")
    mensagem = getattr(exc, "message", None) or str(exc)
    partes.append(str(mensagem)[:420])  # inclui limite/uso/tempo de espera da Groq
    return " | ".join(partes)[:limite]


class IAIndisponivelError(Exception):
    """Nenhuma IA configurada ou todas falharam."""

    def __init__(self, mensagem: str, tentativas: Optional[list] = None):
        super().__init__(mensagem)
        self.tentativas = tentativas or []


@dataclass
class RespostaIA:
    dados: dict
    provedor: str  # nome do slot que respondeu
    modelo: str    # modelo que respondeu (para auditoria)
    fontes: list = field(default_factory=list)  # [{titulo, url}] da pesquisa na web


# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
def _ler_configuracoes(db: Session) -> dict:
    """Lê toda a tabela de configurações como {chave: valor}."""
    try:
        linhas = db.execute(text("SELECT chave, valor FROM configuracoes_sistema")).fetchall()
    except Exception as exc:
        db.rollback()  # não deixa a transação da requisição "envenenada"
        logger.error("Falha ao ler configuracoes_sistema: %s", exc)
        return {}
    return {l[0]: (str(l[1]).strip() if l[1] is not None else "") for l in linhas}


def _chave_do_slot(slot: Slot, config: dict) -> str:
    """Chave salva no banco; se vazia (ou mascarada), usa variável de ambiente."""
    valor = config.get(slot.chave_api, "")
    if valor and not valor.startswith("****"):
        return valor
    for env in slot.envs:
        reserva = os.getenv(env)
        if reserva and reserva.strip():
            return reserva.strip()
    return ""


# --------------------------------------------------------------------------- #
# Chamada aos provedores
# --------------------------------------------------------------------------- #
def _chamar_provedor(slot: Slot, modelo: str, api_key: str, prompt: str, json_mode: bool,
                     timeout: int = TIMEOUT_SEGUNDOS) -> str:
    """Faz UMA chamada ao provedor e devolve o texto bruto. Lança exceção se falhar."""
    if slot.tipo == "gemini":
        cliente = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout * 1000),  # em milissegundos
        )
        # Gemini 3.x: NÃO enviar temperature/top_p/top_k (parâmetros descontinuados,
        # a API rejeita ou ignora). Só o formato de saída é configurado.
        config = types.GenerateContentConfig(
            response_mime_type="application/json" if json_mode else None,
        )
        resposta = cliente.models.generate_content(model=modelo, contents=prompt, config=config)
        return resposta.text or ""

    # Groq (API compatível com OpenAI). max_retries=0 para o fallback ser rápido.
    cliente = OpenAI(base_url=GROQ_BASE_URL, api_key=api_key,
                     timeout=timeout, max_retries=0)
    args = {
        "model": modelo,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    if json_mode:
        try:
            completion = cliente.chat.completions.create(
                **args, response_format={"type": "json_object"}
            )
        except BadRequestError:
            # Modelo não aceita "modo JSON": tenta sem; o parser abaixo extrai o JSON.
            completion = cliente.chat.completions.create(**args)
    else:
        completion = cliente.chat.completions.create(**args)
    return completion.choices[0].message.content or ""


def _chamar_gemini_com_busca(modelo: str, api_key: str, prompt: str, timeout: int):
    """
    Gemini com a ferramenta de Pesquisa do Google. Devolve (texto, fontes), onde
    `fontes` são as páginas realmente consultadas ([{titulo, url}]).
    Obs.: com ferramentas ativas o "modo JSON" não é usado; o parser extrai o JSON.
    """
    cliente = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=timeout * 1000),  # milissegundos
    )
    config = types.GenerateContentConfig(  # sem temperature (ver nota em _chamar_provedor)
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )
    resposta = cliente.models.generate_content(model=modelo, contents=prompt, config=config)

    fontes, vistas = [], set()
    try:
        meta = resposta.candidates[0].grounding_metadata
        for pedaco in (meta.grounding_chunks or []):
            web = pedaco.web
            if web and web.uri and web.uri not in vistas:
                vistas.add(web.uri)
                fontes.append({"titulo": web.title or "", "url": web.uri})
    except (AttributeError, IndexError, TypeError):
        pass  # sem metadados de ancoragem -> fontes vazias -> resposta rejeitada
    return resposta.text or "", fontes


def _groq_tem_busca(modelo: str) -> bool:
    """Segundo a documentação da Groq, `browser_search` existe nos modelos openai/gpt-oss-*."""
    return (modelo or "").strip().lower().startswith("openai/gpt-oss")


def _coletar_fontes(no, achadas: list, vistas: set) -> None:
    """Percorre a estrutura de `executed_tools` e junta toda {url, title} encontrada."""
    if isinstance(no, dict):
        url = no.get("url")
        if isinstance(url, str) and url.startswith("http") and url not in vistas:
            vistas.add(url)
            achadas.append({"titulo": str(no.get("title") or no.get("titulo") or ""), "url": url})
        for valor in no.values():
            _coletar_fontes(valor, achadas, vistas)
    elif isinstance(no, (list, tuple)):
        for item in no:
            _coletar_fontes(item, achadas, vistas)
    elif hasattr(no, "model_dump"):  # objetos pydantic do SDK
        _coletar_fontes(no.model_dump(), achadas, vistas)


def _chamar_groq_com_busca(modelo: str, api_key: str, prompt: str, timeout: int):
    """
    Groq com a ferramenta `browser_search` (modelos openai/gpt-oss-*). Devolve (texto, fontes).
    A Groq informa o que foi pesquisado em `message.executed_tools`; sem isso, a resposta
    saiu da memória do modelo e é recusada. Obs.: browser_search não combina com
    "structured outputs", por isso não enviamos response_format aqui.
    """
    cliente = OpenAI(base_url=GROQ_BASE_URL, api_key=api_key, timeout=timeout, max_retries=0)
    pedido = dict(
        model=modelo,
        messages=[{"role": "user", "content": prompt}],
        tools=[{"type": "browser_search"}],
        tool_choice="required",
        extra_body={"reasoning_effort": "low"},  # recomendado pela Groq para busca
    )
    try:
        completion = cliente.chat.completions.create(**pedido)
    except InternalServerError:
        # Erro 5xx do lado da Groq costuma ser passageiro: uma nova tentativa depois de uma pausa.
        # (Timeout NÃO é repetido, para não dobrar a espera.)
        logger.warning("Groq devolveu erro 5xx na pesquisa; tentando mais uma vez.")
        time.sleep(3)
        completion = cliente.chat.completions.create(**pedido)
    mensagem = completion.choices[0].message
    executadas = getattr(mensagem, "executed_tools", None)
    if executadas is None:
        executadas = (getattr(mensagem, "model_extra", None) or {}).get("executed_tools")

    fontes: list = []
    _coletar_fontes(executadas, fontes, set())
    if executadas and not fontes:
        # A Groq rodou a busca, mas a estrutura veio diferente do esperado: registra o
        # formato para ajustarmos o leitor, sem gravar o conteúdo das páginas.
        trecho = str(executadas)[:300].replace("\n", " ")
        logger.warning("executed_tools sem URLs reconhecíveis. Estrutura: %s", trecho)
    return mensagem.content or "", fontes


def _extrair_json(texto: str) -> dict:
    """Extrai o objeto JSON da resposta, tolerando ```json, <think> e texto extra."""
    limpo = re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL | re.IGNORECASE).strip()
    inicio, fim = limpo.find("{"), limpo.rfind("}")
    if inicio == -1 or fim <= inicio:
        raise ValueError("A resposta da IA não contém um objeto JSON.")
    objeto = json.loads(limpo[inicio:fim + 1])
    if not isinstance(objeto, dict):
        raise ValueError("A resposta da IA não é um objeto JSON.")
    return objeto


# --------------------------------------------------------------------------- #
# API pública
# --------------------------------------------------------------------------- #
def gerar_json(
    db: Session,
    prompt: str,
    validar: Optional[Callable[[dict], dict]] = None,
    pesquisa_web: bool = False,
    timeout: Optional[int] = None,
    validar_fontes: Optional[Callable[[list], None]] = None,
) -> RespostaIA:
    """
    Pede um JSON à IA, com fallback automático entre os slots configurados.

    `validar` recebe o dict e devolve o dict normalizado, ou levanta ValueError
    se o conteúdo não servir. Nesse caso o próximo provedor é tentado.

    `pesquisa_web=True`: usa só provedores com pesquisa na web (Gemini com Pesquisa do Google,
    ou Groq openai/gpt-oss-* com browser_search) e exige que a resposta venha ancorada em
    pelo menos uma fonte real (RespostaIA.fontes).

    `validar_fontes` (só com pesquisa_web) recebe a lista [{titulo, url}] das páginas consultadas
    e levanta ValueError se não servir (ex.: nenhuma é de uma fonte oficial da clínica); nesse
    caso o próximo provedor é tentado.
    """
    config = _ler_configuracoes(db)
    tentativas: list[str] = []

    for slot in SLOTS_ORDEM:
        modelo = config.get(slot.chave_modelo, "")
        api_key = _chave_do_slot(slot, config)
        if not modelo or not api_key:
            continue  # slot não configurado
        if pesquisa_web and slot.tipo == "groq" and not _groq_tem_busca(modelo):
            continue  # este modelo Groq não tem pesquisa na web (ex.: qwen)

        try:
            fontes: list = []
            if pesquisa_web:
                buscar = _chamar_gemini_com_busca if slot.tipo == "gemini" else _chamar_groq_com_busca
                bruto, fontes = buscar(modelo, api_key, prompt, timeout or TIMEOUT_PESQUISA_SEGUNDOS)
                if not fontes:
                    raise ValueError("A IA respondeu sem consultar fontes na web.")
                if validar_fontes:
                    validar_fontes(fontes)
            else:
                bruto = _chamar_provedor(slot, modelo, api_key, prompt, json_mode=True,
                                         timeout=timeout or TIMEOUT_SEGUNDOS)
            dados = _extrair_json(bruto)
            if validar:
                dados = validar(dados)
            return RespostaIA(dados=dados, provedor=slot.nome, modelo=modelo, fontes=fontes)
        except Exception as exc:  # qualquer falha -> próximo provedor
            registro = f"{slot.nome}/{modelo}: {_descrever_erro(exc)}"
            logger.warning("IA falhou, tentando o próximo provedor. %s", registro)
            tentativas.append(registro)

    if not tentativas:
        raise IAIndisponivelError(
            "Nenhum provedor com pesquisa na web configurado: use o Gemini (plano pago) ou um modelo "
            "Groq openai/gpt-oss-* em Configurações de IA."
            if pesquisa_web else
            "Nenhuma IA configurada. Cadastre modelo e chave em Configurações de IA."
        )
    raise IAIndisponivelError("Todas as IAs configuradas falharam.", tentativas)


def testar_slot(db: Session, nome_slot: str, modelo: Optional[str]) -> dict:
    """
    Teste de conexão usado pela tela de Configurações.
    Usa exatamente o mesmo caminho de código da triagem. A chave testada é a SALVA
    no banco (ou a variável de ambiente), então salve antes de testar uma chave nova.
    """
    slot = next((s for s in SLOTS_ORDEM if s.nome == nome_slot), None)
    if slot is None:
        return {"sucesso": False, "erro": "Provedor desconhecido."}

    config = _ler_configuracoes(db)
    modelo = (modelo or "").strip() or config.get(slot.chave_modelo, "")
    if not modelo:
        return {"sucesso": False, "erro": "Informe o identificador do modelo."}

    api_key = _chave_do_slot(slot, config)
    if not api_key:
        return {"sucesso": False, "erro": "Chave de API não configurada para este provedor."}

    try:
        resposta = _chamar_provedor(
            slot, modelo, api_key, "Responda apenas: 'Conexao bem sucedida!'", json_mode=False
        )
        return {"sucesso": True, "resposta": resposta.strip()}
    except Exception as exc:
        return {"sucesso": False, "erro": _descrever_erro(exc, 900)}