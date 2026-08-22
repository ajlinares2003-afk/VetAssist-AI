from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from schemas.tutor import TutorCreate
from database.database import get_db
from models.tutor import Tutor
from services.security import (
    obter_usuario_logado,
    exigir_perfil
)

router = APIRouter(
    prefix="/tutores",
    tags=["Tutores"]
)

@router.get("/")
def listar_tutores(
    usuario_logado: str = Depends(
        obter_usuario_logado
    ),
    db: Session = Depends(get_db)
):
    tutores = db.query(Tutor).all()

    return tutores

@router.post("/")
def criar_tutor(
    tutor: TutorCreate,
    usuario_logado = Depends(
        exigir_perfil(
            [
                "ADMIN",
                "VETERINARIO",
                "RECEPCAO"
            ]
        )
    ),
    db: Session = Depends(get_db)
):

    tutor_existente = (
        db.query(Tutor)
        .filter(
            (Tutor.cpf == tutor.cpf) |
            (Tutor.email == tutor.email)
        )
        .first()
    )

    if tutor_existente:
        raise HTTPException(
            status_code=409,
            detail="CPF ou Email ja cadastrado"
        )

    novo_tutor = Tutor(
        nome=tutor.nome,
        cpf=tutor.cpf,
        telefone=tutor.telefone,
        email=tutor.email
    )
    
    try:

        db.add(novo_tutor)
        db.commit()
        db.refresh(novo_tutor)

    except IntegrityError:

        db.rollback()

        raise HTTPException(
            status_code=409,
            detail="CPF ou Email ja cadastrado"
        )

    return {
        "id": novo_tutor.id,
        "nome": novo_tutor.nome,
        "cpf": novo_tutor.cpf
    }

@router.get("/{tutor_id}")
def buscar_tutor(
    tutor_id: int,
    db: Session = Depends(get_db)
):
    tutor = (
        db.query(Tutor)
        .filter(Tutor.id == tutor_id)
        .first()
    )

    if not tutor:
        raise HTTPException(
            status_code=404,
            detail="Tutor nao encontrado"
        )
    
    return tutor

@router.delete("/{tutor_id}")
def excluir_tutor(
    tutor_id: int,
    usuario_logado = Depends(
        exigir_perfil(
            [
            "ADMIN",
            "VETERINARIO",
            "RECEPCAO"
            ]
        )
    ),
    db: Session = Depends(get_db)
):
    tutor = (
        db.query(Tutor)
        .filter(Tutor.id == tutor_id)
        .first()
    )

    if not tutor:
        raise HTTPException(
            status_code=404,
            detail="Tutor nao encontrado"
        )

    db.delete(tutor)
    db.commit()

    return {
        "mensagem": "Tutor excluido com sucesso"
    }

@router.put("/{tutor_id}")
def atualizar_tutor(
    tutor_id: int,
    tutor: TutorCreate,
    usuario_logado = Depends(
        exigir_perfil(
            [
            "ADMIN",
            "VETERINARIO",
            "RECEPCAO"
            ]
        )
    ),
    db: Session = Depends(get_db)
):
    tutor_db = (
        db.query(Tutor)
        .filter(Tutor.id == tutor_id)
        .first()
    )

    if not tutor_db:
        raise HTTPException(
            status_code=404,
            detail="Tutor nao encontrado"
        )

    tutor_existente = (
        db.query(Tutor)
        .filter(
            (
                (Tutor.cpf == tutor.cpf) |
                (Tutor.email == tutor.email)
            ) &
            (Tutor.id != tutor_id)
        )
        .first()
    )

    if tutor_existente:
        raise HTTPException(
            status_code=409,
            detail="CPF ou Email ja cadastrado"
        )
    
    tutor_db.nome = tutor.nome
    tutor_db.cpf = tutor.cpf
    tutor_db.telefone = tutor.telefone
    tutor_db.email = tutor.email

    try:

        db.commit()
        db.refresh(tutor_db)

    except IntegrityError:

        db.rollback()

        raise HTTPException(
            status_code=409,
            detail="CPF ou Email ja cadastrado"
            )

    return {
        "id": tutor_db.id,
        "nome": tutor_db.nome,
        "cpf": tutor_db.cpf,
        "telefone": tutor_db.telefone,
        "email": tutor_db.email
    }