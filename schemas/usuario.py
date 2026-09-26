from pydantic import BaseModel, ConfigDict
from typing import Optional


class UsuarioCreate(BaseModel):
    nome: str
    email: str
    senha: str
    perfil: str
    crmv: Optional[str] = None


class UsuarioResponse(BaseModel):
    id: int
    nome: str
    email: str
    perfil: str
    crmv: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True
    )