"""
routers/configuracoes.py

Configurações de IA (modelos e chaves). Acesso EXCLUSIVO do perfil ADMIN.
A lógica de chamada/fallback fica em services/ia_service.py.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from database.database import get_db
from services.ia_service import CAMPOS_CONFIG_IA, testar_slot
from services.referencia_auto import limpar_falhas_pesquisa
from services.security import exigir_perfil

logger = logging.getLogger("vetassist.config")

router = APIRouter(prefix="/configuracoes", tags=["Configurações de IA"])


class ConfigIAUpdate(BaseModel):
    """Só estes campos podem ser gravados (lista fechada)."""
    gemini_model: Optional[str] = None
    gemini_api_key: Optional[str] = None
    groq_model_1: Optional[str] = None
    groq_api_key_1: Optional[str] = None
    groq_model_2: Optional[str] = None
    groq_api_key_2: Optional[str] = None


class TesteIA(BaseModel):
    provedor: str  # "gemini", "groq_1" ou "groq_2"
    modelo: Optional[str] = None


def _mascarar(valor: str) -> str:
    """Mostra só os 4 últimos caracteres da chave."""
    return f"****{valor[-4:]}" if len(valor) > 4 else "****"


@router.get("/ia")
def obter_configuracoes_ia(
    usuario_logado=Depends(exigir_perfil(["ADMIN"])),
    db: Session = Depends(get_db),
):
    resultado = {campo: "" for campo in CAMPOS_CONFIG_IA}
    consulta = text(
        "SELECT chave, valor FROM configuracoes_sistema WHERE chave IN :chaves"
    ).bindparams(bindparam("chaves", expanding=True))

    try:
        linhas = db.execute(consulta, {"chaves": list(CAMPOS_CONFIG_IA)}).fetchall()
    except Exception:
        db.rollback()
        logger.exception("Erro ao ler configurações de IA")
        raise HTTPException(status_code=500, detail="Erro ao ler as configurações de IA.")

    for chave, valor in linhas:
        valor = (valor or "").strip()
        resultado[chave] = _mascarar(valor) if ("api_key" in chave and valor) else valor
    return resultado


@router.put("/ia")
def atualizar_configuracoes_ia(
    payload: ConfigIAUpdate,
    usuario_logado=Depends(exigir_perfil(["ADMIN"])),
    db: Session = Depends(get_db),
):
    # Requer UNIQUE em configuracoes_sistema(chave). Veja o SQL de migração.
    upsert = text(
        "INSERT INTO configuracoes_sistema (chave, valor) VALUES (:c, :v) "
        "ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor"
    )
    try:
        for campo in CAMPOS_CONFIG_IA:
            valor = getattr(payload, campo)
            if valor is None:
                continue  # campo não enviado: mantém o que está no banco
            valor = valor.strip()
            if "api_key" in campo and valor.startswith("****"):
                continue  # chave mascarada devolvida pelo GET: não sobrescrever
            db.execute(upsert, {"c": campo, "v": valor})
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Erro ao salvar configurações de IA")
        raise HTTPException(status_code=500, detail="Erro ao salvar as configurações de IA.")

    limpar_falhas_pesquisa()  # configuração mudou: libera novas tentativas de pesquisa
    return {"mensagem": "Configurações de IA atualizadas com sucesso!"}


@router.post("/ia/testar")
def testar_modelo_ia(
    payload: TesteIA,
    usuario_logado=Depends(exigir_perfil(["ADMIN"])),
    db: Session = Depends(get_db),
):
    resultado = testar_slot(db, payload.provedor.strip().lower(), payload.modelo)
    if resultado.get("sucesso"):
        limpar_falhas_pesquisa()
    return resultado