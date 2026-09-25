from pydantic import BaseModel, ConfigDict
from typing import Optional

class TutorBase(BaseModel):
    codigo: Optional[str] = None
    nome: str
    cpf: str
    telefone: str
    email: Optional[str] = None
    cep: Optional[str] = None
    rua: Optional[str] = None
    complemento: Optional[str] = None
    bairro: Optional[str] = None
    cidade: Optional[str] = None
    estado: Optional[str] = None

class TutorCreate(TutorBase):
    pass

class TutorResponse(TutorBase):
    id: int

    model_config = ConfigDict(from_attributes=True)