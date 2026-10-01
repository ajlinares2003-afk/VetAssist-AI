from pydantic import BaseModel, ConfigDict, Field
from typing import Optional

class AnimalCreate(BaseModel):
    codigo: Optional[str] = None
    nome: str = Field(..., min_length=1)
    especie: str
    sub_especie: Optional[str] = None
    raca: str = Field(..., min_length=1)
    sexo: str
    idade: float = Field(ge=0)
    peso: Optional[float] = None  # <-- Alterado para opcional
    tutor_id: int
    status: str
    castrado: Optional[str] = None
    cor: Optional[str] = None
    porte: Optional[str] = None


class AnimalResponse(BaseModel):
    id: int
    codigo: Optional[str] = None
    nome: str
    especie: str
    sub_especie: Optional[str] = None
    raca: str
    sexo: str
    idade: float
    peso: Optional[float] = None  # <-- Alterado para opcional
    tutor_id: int
    status: str
    castrado: Optional[str] = None
    cor: Optional[str] = None
    porte: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True
    )