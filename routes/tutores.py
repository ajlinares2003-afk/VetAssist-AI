from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from schemas.tutor import TutorCreate, TutorResponse
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
    usuario_logado: str = Depends(obter_usuario_logado),
    db: Session = Depends(get_db)
):
    tutores = db.query(Tutor).order_by(Tutor.id.desc()).all()
    return tutores

@router.post("/")
def criar_tutor(
    tutor: TutorCreate,
    usuario_logado = Depends(
        exigir_perfil(["ADMIN", "VETERINARIO", "RECEPCAO"])
    ),
    db: Session = Depends(get_db)
):
    # Valida duplicação de CPF ou E-mail
    if tutor.cpf:
        tutor_cpf = db.query(Tutor).filter(Tutor.cpf == tutor.cpf).first()
        if tutor_cpf:
            raise HTTPException(
                status_code=409,
                detail="CPF já cadastrado para outro tutor."
            )

    if tutor.email:
        tutor_email = db.query(Tutor).filter(Tutor.email == tutor.email).first()
        if tutor_email:
            raise HTTPException(
                status_code=409,
                detail="E-mail já cadastrado para outro tutor."
            )

    novo_tutor = Tutor(
        codigo=tutor.codigo,  # Aceita código manual caso enviado
        nome=tutor.nome,
        cpf=tutor.cpf,
        telefone=tutor.telefone,
        email=tutor.email
    )

    try:
        db.add(novo_tutor)
        db.flush()  # Gera o ID no banco sem fechar a transação

        # Se não foi informado código manual, gera automático no formato TUT-0001
        if not novo_tutor.codigo:
            novo_tutor.codigo = f"TUT-{novo_tutor.id:04d}"

        db.commit()
        db.refresh(novo_tutor)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Erro ao cadastrar. Verifique se o Código, CPF ou E-mail já existem."
        )

    return novo_tutor

@router.get("/{tutor_id}")
def buscar_tutor(
    tutor_id: int,
    db: Session = Depends(get_db)
):
    tutor = db.query(Tutor).filter(Tutor.id == tutor_id).first()
    if not tutor:
        raise HTTPException(
            status_code=404,
            detail="Tutor não encontrado."
        )
    return tutor

@router.delete("/{tutor_id}")
def excluir_tutor(
    tutor_id: int,
    usuario_logado = Depends(
        exigir_perfil(["ADMIN", "VETERINARIO", "RECEPCAO"])
    ),
    db: Session = Depends(get_db)
):
    tutor = db.query(Tutor).filter(Tutor.id == tutor_id).first()
    if not tutor:
        raise HTTPException(
            status_code=404,
            detail="Tutor não encontrado."
        )

    try:
        db.delete(tutor)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não é possível excluir o tutor pois existem animais (pacientes) cadastrados em seu nome."
        )

    return {"mensagem": "Tutor excluído com sucesso."}

@router.put("/{tutor_id}")
def atualizar_tutor(
    tutor_id: int,
    tutor: TutorCreate,
    usuario_logado = Depends(
        exigir_perfil(["ADMIN", "VETERINARIO", "RECEPCAO"])
    ),
    db: Session = Depends(get_db)
):
    tutor_db = db.query(Tutor).filter(Tutor.id == tutor_id).first()
    if not tutor_db:
        raise HTTPException(
            status_code=404,
            detail="Tutor não encontrado."
        )

    # Conflito de Código em outro tutor
    if tutor.codigo and tutor.codigo != tutor_db.codigo:
        cod_existente = (
            db.query(Tutor)
            .filter(Tutor.codigo == tutor.codigo, Tutor.id != tutor_id)
            .first()
        )
        if cod_existente:
            raise HTTPException(
                status_code=409,
                detail=f"O código '{tutor.codigo}' já pertence ao tutor {cod_existente.nome}."
            )

    # Conflito de CPF em outro tutor
    if tutor.cpf and tutor.cpf != tutor_db.cpf:
        cpf_existente = (
            db.query(Tutor)
            .filter(Tutor.cpf == tutor.cpf, Tutor.id != tutor_id)
            .first()
        )
        if cpf_existente:
            raise HTTPException(
                status_code=409,
                detail="CPF já cadastrado para outro tutor."
            )

    # Conflito de Email em outro tutor
    if tutor.email and tutor.email != tutor_db.email:
        email_existente = (
            db.query(Tutor)
            .filter(Tutor.email == tutor.email, Tutor.id != tutor_id)
            .first()
        )
        if email_existente:
            raise HTTPException(
                status_code=409,
                detail="E-mail já cadastrado para outro tutor."
            )

    if tutor.codigo:
        tutor_db.codigo = tutor.codigo

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
            detail="Ocorreu um conflito de dados (CPF, E-mail ou Código em uso)."
        )

    return tutor_db