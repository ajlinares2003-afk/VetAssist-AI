from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session
from database.database import get_db
from models.exame import Exame
from models.consulta import Consulta
from schemas.exame import ExameCreate
from services.security import obter_usuario_logado

router = APIRouter(
    prefix="/exames",
    tags=["Exames"]
)

@router.get("/")
def listar_exames(
    db: Session = Depends(get_db)
):
    exames = db.query(Exame).all()

    return exames

@router.post("/")
def criar_exame(
    exame: ExameCreate,
    usuario_logado: str = Depends(
        obter_usuario_logado
    ),
    db: Session = Depends(get_db)
):
    consulta = (
        db.query(Consulta)
        .filter(Consulta.id == exame.consulta_id)
        .first()
    )

    if not consulta:
        return {
            "erro": "Consulta nao encontrada"
        }

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
def buscar_exame(
    exame_id: int,
    db: Session = Depends(get_db)
):
    exame = (
        db.query(Exame)
        .filter(Exame.id == exame_id)
        .first()
    )

    return exame

@router.put("/{exame_id}")
def atualizar_exame(
    exame_id: int,
    exame: ExameCreate,
    usuario_logado: str = Depends(
        obter_usuario_logado
    ),
    db: Session = Depends(get_db)
):
    exame_db = (
        db.query(Exame)
        .filter(Exame.id == exame_id)
        .first()
    )

    if not exame_db:
        return {
            "erro": "Exame nao encontrado"
        }

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
    usuario_logado: str = Depends(
        obter_usuario_logado
    ),
    db: Session = Depends(get_db)
):
    exame = (
        db.query(Exame)
        .filter(Exame.id == exame_id)
        .first()
    )

    if not exame:
        return {
        "erro": "Exame nao encontrado"
        }

    db.delete(exame)
    db.commit()

    return {
        "mensagem": "Exame excluido com sucesso"
    }