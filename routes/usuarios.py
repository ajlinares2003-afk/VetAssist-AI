from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from schemas.usuario import UsuarioCreate
from database.database import get_db
from models.usuario import Usuario
from services.security import (
    obter_usuario_logado,
    exigir_perfil,
    gerar_hash
)

router = APIRouter(
    prefix="/usuarios",
    tags=["Usuarios"]
)

@router.get("/")
def listar_usuarios(
    usuario_logado: str = Depends(
        obter_usuario_logado
    ),
    db: Session = Depends(get_db)
):
    usuarios = db.query(Usuario).all()
    return usuarios

@router.get("/{usuario_id}")
def buscar_usuario(
    usuario_id: int,
    db: Session = Depends(get_db)
):
    usuario = (
        db.query(Usuario)
        .filter(Usuario.id == usuario_id)
        .first()
    )
    if not usuario:
        raise HTTPException(
            status_code=404,
            detail="Usuario nao encontrado"
        )
    return usuario

@router.post("/")
def criar_usuario(
    usuario: UsuarioCreate,
    usuario_logado = Depends(
        exigir_perfil(
            ["ADMIN","VETERINARIO"]
        )
    ),

    db: Session = Depends(get_db)
):

    usuario_existente = (
        db.query(Usuario)
        .filter(
            Usuario.email == usuario.email
        )
        .first()
    )

    if usuario_existente:
        raise HTTPException(
            status_code=409,
            detail="Email ja cadastrado"
        )

    novo_usuario = Usuario(
        nome=usuario.nome,
        email=usuario.email,
        senha_hash=gerar_hash(
            usuario.senha
        ),
        perfil=usuario.perfil,
         ativo=True
    )
    
    try:

        db.add(novo_usuario)
        db.commit()
        db.refresh(novo_usuario)

    except IntegrityError:

        db.rollback()

        raise HTTPException(
            status_code=409,
            detail="Email ja cadastrado"
        )

    return {
        "id": novo_usuario.id,
        "nome": novo_usuario.nome,
        "email": novo_usuario.email,
        "perfil": novo_usuario.perfil
    }

@router.delete("/{usuario_id}")
def excluir_usuario(
    usuario_id: int,
    usuario_logado = Depends(
        exigir_perfil(
            ["ADMIN","VETERINARIO"]
        )
    ),
    db: Session = Depends(get_db)
):
    usuario = (
        db.query(Usuario)
        .filter(Usuario.id == usuario_id)
        .first()
    )

    if not usuario:
        raise HTTPException(
            status_code=404,
            detail="Usuario nao encontrado"
        )

    db.delete(usuario)
    db.commit()

    return {
        "mensagem": "Usuario excluido com sucesso"
    }

@router.put("/{usuario_id}")
def atualizar_usuario(
    usuario_id: int,
    usuario: UsuarioCreate,
    usuario_logado = Depends(
        exigir_perfil(
            ["ADMIN","VETERINARIO"]
        )
    ),
    db: Session = Depends(get_db)
):
    usuario_db = (
        db.query(Usuario)
        .filter(Usuario.id == usuario_id)
        .first()
    )

    if not usuario_db:
        raise HTTPException(
            status_code=404,
            detail="Usuario nao encontrado"
        )

    usuario_db.nome = usuario.nome
    email_existente = (
        db.query(Usuario)
        .filter(
            Usuario.email == usuario.email,
            Usuario.id != usuario_id
        )
        .first()
    )

    if email_existente:
        raise HTTPException(
            status_code=409,
            detail="Email ja cadastrado"
        )

    usuario_db.email = usuario.email
    usuario_db.senha_hash = gerar_hash(
        usuario.senha
    )
    usuario_db.perfil = usuario.perfil

    try:

        db.commit()
        db.refresh(usuario_db)

    except IntegrityError:

        db.rollback()

        raise HTTPException(
            status_code=409,
            detail="Email ja cadastrado"
        )

    return {
        "id": usuario_db.id,
        "nome": usuario_db.nome,
        "email": usuario_db.email,
        "perfil": usuario_db.perfil
    }