from pydantic import BaseModel

class AnimalCreate(BaseModel):
    nome: str
    especie: str
    raca: str
    sexo: str
    idade: int
    peso: float
    tutor_id: int


class AnimalResponse(BaseModel):
    id: int
    nome: str
    especie: str
    tutor_id: int

    class Config:
        from_attributes = True