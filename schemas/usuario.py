from pydantic import BaseModel, ConfigDict


class UsuarioCreate(BaseModel):
    nome: str
    email: str
    senha: str
    perfil: str


class UsuarioResponse(BaseModel):
    id: int
    nome: str
    email: str
    perfil: str

    model_config = ConfigDict(
        from_attributes=True
    )