from pydantic import BaseModel, ConfigDict

class TutorCreate(BaseModel):
    nome: str
    telefone: str
    email: str


class TutorResponse(BaseModel):
    id: int
    nome: str
    telefone: str
    email: str

    model_config = ConfigDict(
        from_attributes=True
    )