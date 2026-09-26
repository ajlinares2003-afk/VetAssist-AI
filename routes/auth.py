from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
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

@router.post("/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    email_limpo = form_data.username.strip().lower()
    
    usuario = (
        db.query(Usuario)
        .filter(Usuario.email == email_limpo)
        .first()
    )

    if not usuario or not verificar_senha(form_data.password, usuario.senha_hash):
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