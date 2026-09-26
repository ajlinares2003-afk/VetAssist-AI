from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database.database import get_db
from models.usuario import Usuario
from services.security import (
    verificar_senha,
    criar_token,
    obter_usuario_logado
)

router = APIRouter(
    prefix="/auth",
    tags=["Autenticação"]
)

class LoginSchema(BaseModel):
    email: str | None = None
    senha: str | None = None
    username: str | None = None
    password: str | None = None

@router.post("/login")
def login(
    dados: LoginSchema,
    db: Session = Depends(get_db)
):
    # Aceita tanto email/senha quanto username/password do frontend
    email_input = dados.email or dados.username
    senha_input = dados.senha or dados.password

    if not email_input or not senha_input:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="E-mail/usuário e senha são obrigatórios"
        )

    email_limpo = email_input.strip().lower()
    
    usuario = (
        db.query(Usuario)
        .filter(Usuario.email == email_limpo)
        .first()
    )

    if not usuario or not verificar_senha(senha_input, usuario.senha_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha incorretos"
        )

    perfil_str = usuario.perfil.value if hasattr(usuario.perfil, "value") else str(usuario.perfil)

    token = criar_token(
        {
            "sub": str(usuario.id),
            "perfil": perfil_str,
            "email": usuario.email
        }
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "usuario": {
            "id": usuario.id,
            "nome": usuario.nome,
            "email": usuario.email,
            "perfil": perfil_str
        }
    }

@router.get("/me")
def me(
    usuario_id: str = Depends(obter_usuario_logado),
    db: Session = Depends(get_db)
):
    usuario = db.query(Usuario).filter(Usuario.id == int(usuario_id)).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    perfil_str = usuario.perfil.value if hasattr(usuario.perfil, "value") else str(usuario.perfil)

    return {
        "id": usuario.id,
        "nome": usuario.nome,
        "email": usuario.email,
        "perfil": perfil_str
    }