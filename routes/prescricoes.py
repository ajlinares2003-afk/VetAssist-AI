from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session
from database.database import get_db
from models.prescricao import Prescricao
from models.consulta import Consulta
from schemas.prescricao import PrescricaoCreate
from services.security import (
    obter_usuario_logado,
    exigir_perfil
)
router = APIRouter(
    prefix="/prescricoes",
    tags=["Prescrições"]
)

@router.get("/")
def listar_prescricoes(
    db: Session = Depends(get_db)
):
    prescricoes = db.query(Prescricao).all()
    return prescricoes

@router.post("/")
def criar_prescricao(
    prescricao: PrescricaoCreate,
    usuario_logado: str = Depends(
        obter_usuario_logado
    ),
    db: Session = Depends(get_db)
):
    consulta = (
        db.query(Consulta)
        .filter(Consulta.id == prescricao.consulta_id)
        .first()
    )

    if not consulta:
        return {
            "erro": "Consulta nao encontrada"
        }

    nova_prescricao = Prescricao(
        consulta_id=prescricao.consulta_id,
        medicamento=prescricao.medicamento,
        dosagem=prescricao.dosagem,
        frequencia=prescricao.frequencia,
        duracao=prescricao.duracao,
        observacoes=prescricao.observacoes
    )

    db.add(nova_prescricao)
    db.commit()
    db.refresh(nova_prescricao)
    return nova_prescricao

@router.get("/{prescricao_id}")
def buscar_prescricao(
    prescricao_id: int,
    db: Session = Depends(get_db)
):
    prescricao = (
        db.query(Prescricao)
        .filter(Prescricao.id == prescricao_id)
        .first()
    )
    return prescricao

@router.put("/{prescricao_id}")
def atualizar_prescricao(
    prescricao_id: int,
    prescricao: PrescricaoCreate,
    usuario_logado = Depends(
        exigir_perfil(
            ["ADMIN", "VETERINARIO"]
        )
    ),
    db: Session = Depends(get_db)
):
    prescricao_db = (
        db.query(Prescricao)
        .filter(Prescricao.id == prescricao_id)
        .first()
    )
    if not prescricao_db:
        return {
            "erro": "Prescricao nao encontrada"
        }

    prescricao_db.consulta_id = prescricao.consulta_id
    prescricao_db.medicamento = prescricao.medicamento
    prescricao_db.dosagem = prescricao.dosagem
    prescricao_db.frequencia = prescricao.frequencia
    prescricao_db.duracao = prescricao.duracao
    prescricao_db.observacoes = prescricao.observacoes

    db.commit()
    db.refresh(prescricao_db)
    return prescricao_db

@router.delete("/{prescricao_id}")
def excluir_prescricao(
    prescricao_id: int,
    usuario_logado = Depends(
        exigir_perfil(
            ["ADMIN", "VETERINARIO"]
        )
    ),
    db: Session = Depends(get_db)
):
    prescricao = (
        db.query(Prescricao)
        .filter(Prescricao.id == prescricao_id)
        .first()
    )
    if not prescricao:
        return {
            "erro": "Prescricao nao encontrada"
        }

    db.delete(prescricao)
    db.commit()
    return {
        "mensagem": "Prescricao excluida com sucesso"
    }