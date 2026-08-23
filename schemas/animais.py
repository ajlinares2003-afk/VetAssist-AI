from pydantic import BaseModel, ConfigDict

class AnimalCreate(BaseModel):
    nome: str
    especie: str
    raca: str
    sexo: str
    idade: int
    peso: float
    tutor_id: int
    status: str


class AnimalResponse(BaseModel):
    id: int
    nome: str
    especie: str
    raca: str
    sexo: str
    idade: int
    peso: float
    tutor_id: int
    status: str

    model_config = ConfigDict(
        from_attributes=True
    )