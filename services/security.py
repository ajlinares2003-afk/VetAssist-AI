from datetime import datetime, UTC
from datetime import timedelta
from jose import jwt
from passlib.context import CryptContext
from fastapi import Depends
from fastapi import HTTPException
from fastapi.security import OAuth2PasswordBearer

SECRET_KEY = "vetassist-secret-key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480

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
    expira = datetime.now(UTC) + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
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

        return payload

    except Exception:

        raise HTTPException(
            status_code=401,
            detail="Token inválido"
        )

def exigir_perfil(
    perfis_permitidos: list
):

    def verificar(
        usuario=Depends(
            obter_usuario_logado
        )
    ):

        if usuario["perfil"] not in perfis_permitidos:

            raise HTTPException(
                status_code=403,
                detail="Acesso negado"
            )

        return usuario

    return verificar