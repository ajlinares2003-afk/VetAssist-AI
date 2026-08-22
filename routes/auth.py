from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session
from database.database import get_db
from models.usuario import Usuario
from fastapi.security import OAuth2PasswordRequestForm
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
    usuario = (
        db.query(Usuario)
        .filter(
            Usuario.email == form_data.username
        )
        .first()
    )

    if not usuario:
        return {
            "erro": "Usuário não encontrado"
        }

    if not verificar_senha(
        form_data.password,
        usuario.senha_hash
    ):
        return {
            "erro": "Senha inválida"
        }

    token = criar_token(
        {
            "sub": str(usuario.id),
            "perfil": usuario.perfil
        }
)

    return {
        "access_token": token,
        "token_type": "bearer"
    }

@router.get("/me")
def me(
    usuario_id: str = Depends(
        obter_usuario_logado
    )
):
    return {
        "usuario_id": usuario_id
    }