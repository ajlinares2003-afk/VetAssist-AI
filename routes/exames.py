from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database.database import get_db
from models.exame import Exame
from models.consulta import Consulta
from schemas.exame import ExameCreate
from services.security import obter_usuario_logado, exigir_perfil

router = APIRouter(
    prefix="/exames",
    tags=["Exames"]
)

@router.get("/")
def listar_exames(
    usuario_logado: str = Depends(obter_usuario_logado),
    db: Session = Depends(get_db)
):
    # Retorna do ID mais recente para o mais antigo
    exames = db.query(Exame).order_by(Exame.id.desc()).all()
    return exames

@router.post("/")
def criar_exame(
    exame: ExameCreate,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    consulta = db.query(Consulta).filter(Consulta.id == exame.consulta_id).first()
    if not consulta:
        raise HTTPException(status_code=404, detail="Consulta não encontrada")

    novo_exame = Exame(
        consulta_id=exame.consulta_id,
        tipo_exame=exame.tipo_exame,
        nome_exame=exame.nome_exame,
        data_exame=exame.data_exame,
        resultado=exame.resultado,
        arquivo=exame.arquivo,
        observacoes=exame.observacoes
    )

    db.add(novo_exame)
    db.commit()
    db.refresh(novo_exame)
    return novo_exame

@router.get("/{exame_id}")
def buscar_exame(exame_id: int, db: Session = Depends(get_db)):
    exame = db.query(Exame).filter(Exame.id == exame_id).first()
    if not exame:
        raise HTTPException(status_code=404, detail="Exame não encontrado")
    return exame

@router.put("/{exame_id}")
def atualizar_exame(
    exame_id: int,
    exame: ExameCreate,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    exame_db = db.query(Exame).filter(Exame.id == exame_id).first()
    if not exame_db:
        raise HTTPException(status_code=404, detail="Exame não encontrado")

    consulta = db.query(Consulta).filter(Consulta.id == exame.consulta_id).first()
    if not consulta:
        raise HTTPException(status_code=404, detail="Consulta não encontrada")

    exame_db.consulta_id = exame.consulta_id
    exame_db.tipo_exame = exame.tipo_exame
    exame_db.nome_exame = exame.nome_exame
    exame_db.data_exame = exame.data_exame
    exame_db.resultado = exame.resultado
    exame_db.arquivo = exame.arquivo
    exame_db.observacoes = exame.observacoes

    db.commit()
    db.refresh(exame_db)
    return exame_db

@router.delete("/{exame_id}")
def excluir_exame(
    exame_id: int,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    exame = db.query(Exame).filter(Exame.id == exame_id).first()
    if not exame:
        raise HTTPException(status_code=404, detail="Exame não encontrado")

    db.delete(exame)
    db.commit()
    return {"mensagem": "Exame excluído com sucesso"}