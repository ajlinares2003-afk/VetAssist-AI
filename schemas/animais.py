from pydantic import BaseModel, ConfigDict, Field
from typing import Optional

class AnimalCreate(BaseModel):
    codigo: Optional[str] = None
    nome: str = Field(..., min_length=1)
    especie: str
    raca: str = Field(..., min_length=1)
    sexo: str
    idade: float = Field(ge=0)
    peso: float = Field(ge=0)
    tutor_id: int
    status: str
    castrado: Optional[str] = None  # Novo campo
    cor: Optional[str] = None        # Novo campo
    porte: Optional[str] = None      # Novo campo


class AnimalResponse(BaseModel):
    id: int
    codigo: Optional[str] = None
    nome: str
    especie: str
    raca: str
    sexo: str
    idade: float
    peso: float
    tutor_id: int
    status: str
    castrado: Optional[str] = None  # Novo campo
    cor: Optional[str] = None        # Novo campo
    porte: Optional[str] = None      # Novo campo

    model_config = ConfigDict(
        from_attributes=True
    )