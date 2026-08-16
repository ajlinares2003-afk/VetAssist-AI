from pydantic import BaseModel

class TutorCreate(BaseModel):
    nome: str
    cpf: str
    telefone: str
    email: str

class TutorResponse(BaseModel):
    id: int
    nome: str
    cpf: str
    telefone: str
    email: str

    class Config:
        from_attributes = True