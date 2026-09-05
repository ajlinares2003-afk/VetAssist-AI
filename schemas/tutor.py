from pydantic import BaseModel, ConfigDict
from typing import Optional

class TutorCreate(BaseModel):
    codigo: Optional[str] = None
    nome: str
    cpf: str
    telefone: str
    email: Optional[str] = None


class TutorResponse(BaseModel):
    id: int
    codigo: Optional[str] = None
    nome: str
    cpf: str
    telefone: str
    email: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True
    )