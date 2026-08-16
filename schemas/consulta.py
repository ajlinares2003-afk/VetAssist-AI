from pydantic import BaseModel
from pydantic import ConfigDict

from typing import Optional
from datetime import datetime


class ConsultaBase(BaseModel):

    usuario_id: int

    animal_id: int

    queixa_principal: Optional[str] = None

    historico_clinico: Optional[str] = None

    sintomas: Optional[str] = None

    exame_fisico: Optional[str] = None

    peso_atendimento: Optional[float] = None

    temperatura: Optional[float] = None

    frequencia_cardiaca: Optional[int] = None

    frequencia_respiratoria: Optional[int] = None

    observacoes: Optional[str] = None


class ConsultaCreate(ConsultaBase):
    pass


class ConsultaUpdate(ConsultaBase):
    pass


class ConsultaResponse(ConsultaBase):

    id: int

    data_consulta: datetime

    model_config = ConfigDict(
        from_attributes=True
    )