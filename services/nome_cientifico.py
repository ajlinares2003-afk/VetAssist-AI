"""
services/nome_cientifico.py

Descobre o nome científico (binômio) pelo mesmo caminho das demais IAs do sistema
(`ia_service.gerar_json`: Gemini -> Groq 1 -> Groq 2, chaves lidas de configuracoes_sistema).
Substitui o requests direto à Groq do cadastro de animais, que podia usar a chave de um slot
com o modelo de outro.
"""
import logging
import re
from typing import Optional

from sqlalchemy.orm import Session

from services.ia_service import IAIndisponivelError, gerar_json

logger = logging.getLogger("vetassist.nome_cientifico")
_BINOMIO = re.compile(r"^[A-Z][a-z]+(?: [a-z\-]+){1,2}$")


def _validar(dados: dict) -> dict:
    nome = " ".join(str(dados.get("nome_cientifico") or "").split()).strip().strip(".")
    if not _BINOMIO.match(nome):
        raise ValueError(f"Nome científico fora do formato Gênero espécie: {nome!r}")
    return {"nome_cientifico": nome}


def buscar_nome_cientifico(db: Session, especie, sub_especie, raca) -> Optional[str]:
    prompt = (
        "Informe o nome científico (binômio: Gênero espécie, com subespécie só se for "
        "universalmente usada) do animal abaixo. Se a raça for genérica/SRD ou desconhecida, "
        "use o nome científico da espécie principal.\n"
        f"- Espécie: {especie or 'não informada'}\n"
        f"- Sub-espécie/tipo: {sub_especie or 'não informada'}\n"
        f"- Raça: {raca or 'não informada'}\n\n"
        'Responda SOMENTE com JSON puro: {"nome_cientifico": "Felis catus"}'
    )
    try:
        return gerar_json(db, prompt, validar=_validar).dados["nome_cientifico"]
    except IAIndisponivelError as exc:
        logger.warning("Nome científico indisponível: %s | %s", exc, exc.tentativas)
        return None