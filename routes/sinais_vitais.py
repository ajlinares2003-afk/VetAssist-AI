"""
Sinais vitais que o veterinário registra no atendimento (TPC, mucosas e ECC).

Por que existe: a tabela `consulta` não tinha essas colunas, então o TPC digitado no atendimento era
descartado em silêncio ao salvar e a tela voltava em branco. Agora ficam em consulta.tpc_segundos /
consulta.mucosas / consulta.ecc (colunas criadas pela migração `consulta_sinais_vitais_extras`).
A triagem original (tabela triagem) não é alterada: continua sendo o registro da recepção.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from database import get_db
from services.security import exigir_perfil, obter_usuario_logado

router = APIRouter(prefix="/sinais-vitais", tags=["Sinais vitais do atendimento"])

PERFIS = ["ADMIN", "VETERINARIO", "RECEPCAO"]
STATUS_TRAVADOS = ("Aguardando Pagamento", "Finalizado", "FINALIZADO")


class SinaisVitaisIn(BaseModel):
    tpc_segundos: Optional[float] = None
    mucosas: Optional[str] = None
    ecc: Optional[str] = None


def _perfil_de(usuario) -> str:
    if isinstance(usuario, dict):
        return str(usuario.get("perfil") or "").upper()
    return str(getattr(usuario, "perfil", "") or "").upper()


@router.get("/{consulta_id}")
def ler_sinais_vitais(consulta_id: int, db: Session = Depends(get_db), usuario_logado=Depends(obter_usuario_logado)):
    linha = db.execute(
        text("SELECT tpc_segundos, mucosas, ecc FROM consulta WHERE id = :c"), {"c": consulta_id}
    ).fetchone()
    if not linha:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    return {"tpc_segundos": linha[0], "mucosas": linha[1], "ecc": linha[2]}


@router.put("/{consulta_id}")
def gravar_sinais_vitais(
    consulta_id: int,
    dados: SinaisVitaisIn,
    db: Session = Depends(get_db),
    usuario_logado=Depends(exigir_perfil(PERFIS)),
):
    consulta = db.execute(text("SELECT status FROM consulta WHERE id = :c"), {"c": consulta_id}).fetchone()
    if not consulta:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    # Atendimento finalizado fica travado (mesma regra do prontuário): só o ADMIN altera.
    if str(consulta[0]) in STATUS_TRAVADOS and _perfil_de(usuario_logado) != "ADMIN":
        raise HTTPException(status_code=409, detail="Atendimento finalizado: só o administrador pode alterar.")

    if dados.tpc_segundos is not None and not (0 <= dados.tpc_segundos <= 30):
        raise HTTPException(status_code=422, detail="TPC fora do intervalo plausível (0 a 30 segundos).")

    db.execute(
        text("UPDATE consulta SET tpc_segundos = :tpc, mucosas = :muc, ecc = :ecc WHERE id = :c"),
        {"tpc": dados.tpc_segundos, "muc": (dados.mucosas or None), "ecc": (dados.ecc or None), "c": consulta_id},
    )
    db.commit()
    return {"mensagem": "Sinais vitais gravados."}