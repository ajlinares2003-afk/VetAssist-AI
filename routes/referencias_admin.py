"""
routers/referencias_admin.py

Revisão das referências cadastradas automaticamente pela IA (status PENDENTE).
Perfis ADMIN e VETERINARIO podem listar, corrigir+validar ou rejeitar.
Lembre de registrar no main.py:  app.include_router(referencias_admin.router)
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from database.database import get_db
from services.security import exigir_perfil

logger = logging.getLogger("vetassist.referencias")

router = APIRouter(prefix="/referencias", tags=["Referências (validação)"])


class CorrecaoReferencia(BaseModel):
    """Campos opcionais: o veterinário pode corrigir antes de validar."""
    peso_min: Optional[float] = None
    peso_max: Optional[float] = None
    temp_repouso_min: Optional[float] = None
    temp_repouso_max: Optional[float] = None
    temp_clinica_min: Optional[float] = None
    temp_clinica_max: Optional[float] = None
    fc_repouso_min: Optional[int] = None
    fc_repouso_max: Optional[int] = None
    fc_clinica_min: Optional[int] = None
    fc_clinica_max: Optional[int] = None
    fr_repouso_min: Optional[int] = None
    fr_repouso_max: Optional[int] = None
    fr_clinica_min: Optional[int] = None
    fr_clinica_max: Optional[int] = None
    tpc_ref: Optional[str] = None
    mucosas_ref: Optional[str] = None
    ecc_ideal: Optional[str] = None
    fonte_bibliografica: Optional[str] = None


def _quem(usuario) -> str:
    """Nome de quem validou (o formato do retorno de exigir_perfil pode variar)."""
    for atributo in ("nome", "username", "email"):
        valor = getattr(usuario, atributo, None)
        if valor:
            return str(valor)
    return str(usuario)


@router.get("/pendentes")
def listar_pendentes(
    usuario_logado=Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db),
):
    linhas = db.execute(text(
        "SELECT * FROM biblioteca_parametros_oficiais "
        "WHERE status = 'PENDENTE' ORDER BY created_at DESC, id DESC"
    )).mappings().all()
    return [dict(l) for l in linhas]


@router.put("/{referencia_id}/validar")
def validar_referencia(
    referencia_id: int,
    correcao: CorrecaoReferencia,
    usuario_logado=Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db),
):
    existe = db.execute(
        text("SELECT status FROM biblioteca_parametros_oficiais WHERE id = :i"),
        {"i": referencia_id},
    ).first()
    if not existe:
        raise HTTPException(status_code=404, detail="Referência não encontrada.")

    # Só campos da lista fechada do modelo Pydantic entram no UPDATE.
    campos = {k: v for k, v in correcao.dict().items() if v is not None}
    sets = ", ".join(f"{c} = :{c}" for c in campos)
    sql = (
        "UPDATE biblioteca_parametros_oficiais SET "
        + (sets + ", " if sets else "")
        + "status = 'VALIDADO', validado_por = :quem, validado_em = now() WHERE id = :id"
    )
    try:
        db.execute(text(sql), {**campos, "quem": _quem(usuario_logado), "id": referencia_id})
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Erro ao validar referência %s", referencia_id)
        raise HTTPException(status_code=500, detail="Erro ao validar a referência.")
    return {"mensagem": "Referência validada."}


@router.delete("/{referencia_id}/rejeitar")
def rejeitar_referencia(
    referencia_id: int,
    usuario_logado=Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db),
):
    # Só apaga registros gerados pela IA e ainda pendentes; nunca a biblioteca curada.
    try:
        apagadas = db.execute(
            text("DELETE FROM biblioteca_parametros_oficiais "
                 "WHERE id = :i AND origem = 'IA' AND status = 'PENDENTE'"),
            {"i": referencia_id},
        ).rowcount
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Erro ao rejeitar referência %s", referencia_id)
        raise HTTPException(status_code=500, detail="Erro ao rejeitar a referência.")
    if not apagadas:
        raise HTTPException(status_code=404, detail="Referência pendente não encontrada.")
    return {"mensagem": "Referência rejeitada. A IA poderá pesquisar de novo no próximo atendimento."}