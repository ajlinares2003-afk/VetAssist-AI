from datetime import datetime
from datetime import timedelta
from jose import jwt
from passlib.context import CryptContext
from fastapi import Depends
from fastapi import HTTPException
from fastapi.security import OAuth2PasswordBearer

SECRET_KEY = "vetassist-secret-key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/login"
)

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)

def gerar_hash(
    senha: str
):
    return pwd_context.hash(senha)

def verificar_senha(
    senha,
    senha_hash
):
    return pwd_context.verify(
        senha,
        senha_hash
    )

def criar_token(
    dados: dict
):
    dados_token = dados.copy()
    expira = (
        datetime.utcnow() +
        timedelta(
            minutes=ACCESS_TOKEN_EXPIRE_MINUTES
        )
    )

    dados_token.update(
        {
            "exp": expira
        }
    )

    return jwt.encode(
        dados_token,
        SECRET_KEY,
        algorithm=ALGORITHM
    )

def obter_usuario_logado(
    token: str = Depends(oauth2_scheme)
):
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )

        usuario_id = payload.get("sub")
        if not usuario_id:
            raise HTTPException(
                status_code=401,
                detail="Token inválido"
            )

        return usuario_id

    except Exception:

        raise HTTPException(
            status_code=401,
            detail="Token inválido"
        )