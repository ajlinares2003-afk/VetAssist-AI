from pydantic import BaseModel, ConfigDict
from typing import Optional


class UsuarioCreate(BaseModel):
    nome: str
    email: str
    senha: str
    perfil: str
    crmv: Optional[str] = None
    consultorio_padrao: Optional[str] = None  # <-- Adicionado aqui


class UsuarioResponse(BaseModel):
    id: int
    nome: str
    email: str
    perfil: str
    crmv: Optional[str] = None
    consultorio_padrao: Optional[str] = None  # <-- Adicionado aqui também

    model_config = ConfigDict(
        from_attributes=True
    )