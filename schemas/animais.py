from pydantic import BaseModel, ConfigDict, Field
from typing import Optional

class AnimalCreate(BaseModel):
    codigo: Optional[str] = None  # Opcional na criação
    nome: str = Field(..., min_length=1)
    especie: str
    raca: str = Field(..., min_length=1)
    sexo: str
    idade: int = Field(ge=0)
    peso: float = Field(ge=0)
    tutor_id: int
    status: str


class AnimalResponse(BaseModel):
    id: int
    codigo: Optional[str] = None  # Retornado para o Frontend
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