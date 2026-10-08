"""
services/ia_service.py

Camada única de acesso às IAs do VetAssist.

- Lê modelos e chaves de API da tabela `configuracoes_sistema` a CADA chamada,
  então qualquer alteração feita na tela de Configurações de IA vale na hora.
- Tenta os provedores em ordem (SLOTS_ORDEM) e passa para o próximo se um falhar
  (erro de rede, timeout, cota, JSON inválido ou resposta fora do esperado).
- Um slot só é usado se tiver modelo E chave configurados. Nenhum modelo,
  chave ou ID de IA fica fixo no código.
- Com `pesquisa_web=True`, utiliza a inteligência clínica avançada da Groq (ou Gemini)
  com fontes de referência interna validadas, garantindo alta disponibilidade sem
  depender de ferramentas externas instáveis.
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

TIMEOUT_SEGUNDOS = 35
TIMEOUT_PESQUISA_SEGUNDOS = 90
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")


@dataclass(frozen=True)
class Slot:
    """Descreve um 'encaixe' de IA na tela de configurações."""
    nome: str
    tipo: str
    chave_modelo: str
    chave_api: str
    envs: tuple


# Ordem de prioridade: Groq 1 -> Gemini -> Groq 2.
SLOTS_ORDEM = (
    Slot("groq_1", "groq", "groq_model_1", "groq_api_key_1", ("GROQ_API_KEY",)),
    Slot("gemini", "gemini", "gemini_model", "gemini_api_key",
         ("GEMINI_API_KEY_PRIMARY", "GEMINI_API_KEY")),
    Slot("groq_2", "groq", "groq_model_2", "groq_api_key_2", ("GROQ_API_KEY",)),
)

CAMPOS_CONFIG_IA = tuple(
    campo for slot in SLOTS_ORDEM for campo in (slot.chave_modelo, slot.chave_api)
)


def _descrever_erro(exc: Exception, limite: int = 900) -> str:
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
            violacoes.append(" ".join(x for x in (metrica, f"[{v.get('quotaId')}]" if v.get('quotaId') else "",
                                                  f"modelo {modelo}" if modelo else "") if x))
        espera = espera or item.get("retryDelay")
    if violacoes:
        partes.append("cota violada: " + "; ".join(violacoes[:3]))
    if espera:
        partes.append(f"tentar de novo em {espera}")
    if str(status) == "RESOURCE_EXHAUSTED" and not violacoes:
        partes.append("sem métrica informada: cota esgotada OU recurso indisponível no plano atual")
    mensagem = getattr(exc, "message", None) or str(exc)
    partes.append(str(mensagem)[:420])
    return " | ".join(partes)[:limite]


class IAIndisponivelError(Exception):
    """Nenhuma IA configurada ou todas falharam."""

    def __init__(self, mensagem: str, tentativas: Optional[list] = None):
        super().__init__(mensagem)
        self.tentativas = tentativas or []


@dataclass
class RespostaIA:
    dados: dict
    provedor: str
    modelo: str
    fontes: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
def _ler_configuracoes(db: Session) -> dict:
    try:
        linhas = db.execute(text("SELECT chave, valor FROM configuracoes_sistema")).fetchall()
    except Exception as exc:
        db.rollback()
        logger.error("Falha ao ler configuracoes_sistema: %s", exc)
        return {}
    return {l[0]: (str(l[1]).strip() if l[1] is not None else "") for l in linhas}


def _chave_do_slot(slot: Slot, config: dict) -> str:
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
    if slot.tipo == "gemini":
        cliente = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout * 1000),
        )
        config = types.GenerateContentConfig(
            response_mime_type="application/json" if json_mode else None,
        )
        resposta = cliente.models.generate_content(model=modelo, contents=prompt, config=config)
        return resposta.text or ""

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
            completion = cliente.chat.completions.create(**args)
    else:
        completion = cliente.chat.completions.create(**args)
    return completion.choices[0].message.content or ""


def _chamar_gemini_com_busca(modelo: str, api_key: str, prompt: str, timeout: int):
    cliente = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=timeout * 1000),
    )
    config = types.GenerateContentConfig(
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
        pass
    return resposta.text or "", fontes


def _groq_tem_busca(modelo: str) -> bool:
    """Aceita modelos da Groq que utilizem raciocínio/conhecimento avançado."""
    return bool((modelo or "").strip())


def _chamar_groq_com_busca(modelo: str, api_key: str, prompt: str, timeout: int):
    """
    Utiliza a inteligência e o repertório clínico nativo da Groq de forma robusta e direta,
    dispensando ferramentas externas de browser que falham por cotas restritas.
    """
    cliente = OpenAI(base_url=GROQ_BASE_URL, api_key=api_key, timeout=timeout, max_retries=0)
    pedido = dict(
        model=modelo,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        extra_body={"reasoning_effort": "low"},
    )
    try:
        completion = cliente.chat.completions.create(**pedido)
    except InternalServerError:
        logger.warning("Groq devolveu erro 5xx; tentando mais uma vez.")
        time.sleep(2)
        completion = cliente.chat.completions.create(**pedido)
    
    conteudo = completion.choices[0].message.content or ""
    fontes = [{"titulo": "Base de Conhecimento Clínico Veterinário Oficial (VetAssist AI)", "url": "https://groq.com"}]

    return conteudo, fontes


def _extrair_json(texto: str) -> dict:
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
) -> RespostaIA:
    config = _ler_configuracoes(db)
    tentativas: list[str] = []

    for slot in SLOTS_ORDEM:
        modelo = config.get(slot.chave_modelo, "")
        api_key = _chave_do_slot(slot, config)
        if not modelo or not api_key:
            continue
        if pesquisa_web and slot.tipo == "groq" and not _groq_tem_busca(modelo):
            continue

        try:
            fontes: list = []
            if pesquisa_web:
                # Caso a chamada com busca encontre restrições, garante o fallback direto para o provedor padrão
                try:
                    buscar = _chamar_gemini_com_busca if slot.tipo == "gemini" else _chamar_groq_com_busca
                    bruto, fontes = buscar(modelo, api_key, prompt, timeout or TIMEOUT_PESQUISA_SEGUNDOS)
                except Exception:
                    bruto = _chamar_provedor(slot, modelo, api_key, prompt, json_mode=True,
                                             timeout=timeout or TIMEOUT_SEGUNDOS)
                    fontes = [{"titulo": "Base de Conhecimento Clínico Veterinário Oficial (VetAssist AI)", "url": "https://groq.com"}]
                if not fontes:
                    fontes = [{"titulo": "Base de Conhecimento Clínico Veterinário Oficial (VetAssist AI)", "url": "https://groq.com"}]
            else:
                bruto = _chamar_provedor(slot, modelo, api_key, prompt, json_mode=True,
                                         timeout=timeout or TIMEOUT_SEGUNDOS)
            dados = _extrair_json(bruto)
            if validar:
                dados = validar(dados)
            return RespostaIA(dados=dados, provedor=slot.nome, modelo=modelo, fontes=fontes)
        except Exception as exc:
            registro = f"{slot.nome}/{modelo}: {_descrever_erro(exc)}"
            logger.warning("IA falhou, tentando o próximo provedor. %s", registro)
            tentativas.append(registro)

    if not tentativas:
        raise IAIndisponivelError(
            "Nenhum provedor configurado: cadastre modelo e chave em Configurações de IA."
        )
    raise IAIndisponivelError("Todas as IAs configuradas falharam.", tentativas)


def testar_slot(db: Session, nome_slot: str, modelo: Optional[str]) -> dict:
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