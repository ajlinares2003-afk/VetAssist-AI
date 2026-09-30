from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from database.database import get_db
from models.usuario import Usuario, PerfilUsuarioEnum
from schemas.usuario import UsuarioCreate
from services.security import (
    obter_usuario_logado,
    exigir_perfil,
    gerar_hash
)

router = APIRouter(
    prefix="/usuarios",
    tags=["Usuarios & Perfis"]
)


@router.get("/")
def listar_usuarios(
    usuario_logado = Depends(exigir_perfil(["ADMIN", "RECEPCAO", "VETERINARIO", "TRIAGEM"])),
    db: Session = Depends(get_db)
):
    usuarios = db.query(Usuario).order_by(Usuario.id.asc()).all()
    for u in usuarios:
        u.senha_hash = None
    return usuarios


@router.get("/{usuario_id}")
def buscar_usuario(
    usuario_id: int,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "RECEPCAO"])),
    db: Session = Depends(get_db)
):
    usuario = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not usuario:
        raise HTTPException(
            status_code=404,
            detail="Utilizador não encontrado."
        )
    usuario.senha_hash = None
    return usuario


@router.post("/")
def criar_usuario(
    usuario: UsuarioCreate,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "RECEPCAO"])), # <-- Permitido para ADMIN e RECEPCAO
    db: Session = Depends(get_db)
):
    email_limpo = usuario.email.strip().lower()
    
    usuario_existente = db.query(Usuario).filter(Usuario.email == email_limpo).first()
    if usuario_existente:
        raise HTTPException(
            status_code=409,
            detail="Já existe um utilizador cadastrado com este e-mail."
        )

    perfil_str = usuario.perfil.upper() if isinstance(usuario.perfil, str) else usuario.perfil.value
    crmv_valor = getattr(usuario, 'crmv', None)
    if crmv_valor and isinstance(crmv_valor, str):
        crmv_valor = crmv_valor.strip() or None

    consultorio_valor = getattr(usuario, 'consultorio_padrao', None)
    if consultorio_valor and isinstance(consultorio_valor, str):
        consultorio_valor = consultorio_valor.strip() or None

    try:
        novo_usuario = Usuario(
            nome=usuario.nome.strip(),
            email=email_limpo,
            senha_hash=gerar_hash(usuario.senha),
            perfil=perfil_str,
            crmv=crmv_valor if perfil_str == "VETERINARIO" else None,
            consultorio_padrao=consultorio_valor if perfil_str == "VETERINARIO" else None,
            ativo=True
        )
        db.add(novo_usuario)
        db.commit()
        db.refresh(novo_usuario)

        return {
            "id": novo_usuario.id,
            "nome": novo_usuario.nome,
            "email": novo_usuario.email,
            "perfil": str(novo_usuario.perfil),
            "crmv": getattr(novo_usuario, 'crmv', None),
            "consultorio_padrao": getattr(novo_usuario, 'consultorio_padrao', None)
        }
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Já existe um utilizador cadastrado com este e-mail."
        )
    except SQLAlchemyError as err:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"Erro no banco de dados: {str(err.__cause__ or err)}"
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno no servidor: {str(e)}"
        )


@router.put("/{usuario_id}")
def atualizar_usuario(
    usuario_id: int,
    usuario: UsuarioCreate,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "RECEPCAO"])), # <-- Permitido para ADMIN e RECEPCAO
    db: Session = Depends(get_db)
):
    usuario_db = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not usuario_db:
        raise HTTPException(
            status_code=404,
            detail="Utilizador não encontrado."
        )

    email_limpo = usuario.email.strip().lower()
    email_existente = db.query(Usuario).filter(
        Usuario.email == email_limpo,
        Usuario.id != usuario_id
    ).first()

    if email_existente:
        raise HTTPException(
            status_code=409,
            detail="Já existe outro utilizador cadastrado com este e-mail."
        )

    perfil_str = usuario.perfil.upper() if isinstance(usuario.perfil, str) else usuario.perfil.value
    crmv_valor = getattr(usuario, 'crmv', None)
    if crmv_valor and isinstance(crmv_valor, str):
        crmv_valor = crmv_valor.strip() or None

    consultorio_valor = getattr(usuario, 'consultorio_padrao', None)
    if consultorio_valor and isinstance(consultorio_valor, str):
        consultorio_valor = consultorio_valor.strip() or None

    usuario_db.nome = usuario.nome.strip()
    usuario_db.email = email_limpo
    if usuario.senha and usuario.senha.strip():
        usuario_db.senha_hash = gerar_hash(usuario.senha)
    usuario_db.perfil = perfil_str
    usuario_db.crmv = crmv_valor if perfil_str == "VETERINARIO" else None
    usuario_db.consultorio_padrao = consultorio_valor if perfil_str == "VETERINARIO" else None

    try:
        db.commit()
        db.refresh(usuario_db)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Já existe outro utilizador cadastrado com este e-mail."
        )
    except SQLAlchemyError as err:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"Erro ao atualizar no banco: {str(err.__cause__ or err)}"
        )

    return {
        "id": usuario_db.id,
        "nome": usuario_db.nome,
        "email": usuario_db.email,
        "perfil": str(usuario_db.perfil),
        "crmv": getattr(usuario_db, 'crmv', None),
        "consultorio_padrao": getattr(usuario_db, 'consultorio_padrao', None)
    }


@router.delete("/{usuario_id}")
def excluir_usuario(
    usuario_id: int,
    usuario_logado = Depends(exigir_perfil(["ADMIN"])),
    db: Session = Depends(get_db)
):
    usuario = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not usuario:
        raise HTTPException(
            status_code=404,
            detail="Utilizador não encontrado."
        )

    db.delete(usuario)
    db.commit()

    return {
        "mensagem": "Utilizador excluído com sucesso."
    }