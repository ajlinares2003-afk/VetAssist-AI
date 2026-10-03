"""
services/ia_service.py

Camada única de acesso às IAs do VetAssist.

- Lê modelos e chaves de API da tabela `configuracoes_sistema` a CADA chamada,
  então qualquer alteração feita na tela de Configurações de IA vale na hora.
- Tenta os provedores em ordem (SLOTS_ORDEM) e passa para o próximo se um falhar
  (erro de rede, timeout, cota, JSON inválido ou resposta fora do esperado).
- Um slot só é usado se tiver modelo E chave configurados. Nenhum modelo,
  chave ou ID de IA fica fixo no código.
- Se TODOS falharem, levanta IAIndisponivelError. Quem chama decide o que fazer;
  nunca devolva um valor "normal" inventado em caso de falha.
"""
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Callable, Optional

from google import genai
from google.genai import types
from openai import BadRequestError, OpenAI
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("vetassist.ia")

TIMEOUT_SEGUNDOS = 20
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
def _chamar_provedor(slot: Slot, modelo: str, api_key: str, prompt: str, json_mode: bool) -> str:
    """Faz UMA chamada ao provedor e devolve o texto bruto. Lança exceção se falhar."""
    if slot.tipo == "gemini":
        cliente = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=TIMEOUT_SEGUNDOS * 1000),  # em milissegundos
        )
        config = types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json" if json_mode else None,
        )
        resposta = cliente.models.generate_content(model=modelo, contents=prompt, config=config)
        return resposta.text or ""

    # Groq (API compatível com OpenAI). max_retries=0 para o fallback ser rápido.
    cliente = OpenAI(base_url=GROQ_BASE_URL, api_key=api_key,
                     timeout=TIMEOUT_SEGUNDOS, max_retries=0)
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
) -> RespostaIA:
    """
    Pede um JSON à IA, com fallback automático entre os slots configurados.

    `validar` recebe o dict e devolve o dict normalizado, ou levanta ValueError
    se o conteúdo não servir. Nesse caso o próximo provedor é tentado.
    """
    config = _ler_configuracoes(db)
    tentativas: list[str] = []

    for slot in SLOTS_ORDEM:
        modelo = config.get(slot.chave_modelo, "")
        api_key = _chave_do_slot(slot, config)
        if not modelo or not api_key:
            continue  # slot não configurado

        try:
            bruto = _chamar_provedor(slot, modelo, api_key, prompt, json_mode=True)
            dados = _extrair_json(bruto)
            if validar:
                dados = validar(dados)
            return RespostaIA(dados=dados, provedor=slot.nome, modelo=modelo)
        except Exception as exc:  # qualquer falha -> próximo provedor
            registro = f"{slot.nome}/{modelo}: {type(exc).__name__}: {str(exc)[:200]}"
            logger.warning("IA falhou, tentando o próximo provedor. %s", registro)
            tentativas.append(registro)

    if not tentativas:
        raise IAIndisponivelError(
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
        return {"sucesso": False, "erro": f"{type(exc).__name__}: {str(exc)[:300]}"}