from pydantic import BaseModel

class UsuarioCreate(BaseModel):
    nome: str
    email: str
    senha_hash: str
    perfil: str

class UsuarioResponse(BaseModel):
    id: int
    nome: str
    email: str
    perfil: str
    ativo: bool

    class Config:
        from_attributes = True