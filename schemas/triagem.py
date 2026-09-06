from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime
from models.triagem import ClassificacaoRiscoEnum


class TriagemBase(BaseModel):
    consulta_id: int
    peso: Optional[float] = None
    temperatura: Optional[float] = None
    frequencia_cardiaca: Optional[int] = None
    frequencia_respiratoria: Optional[int] = None
    tpc_segundos: Optional[int] = None
    desidratacao_percentual: Optional[int] = None
    queixa_principal: str
    classificacao_risco: ClassificacaoRiscoEnum
    justificativa_risco: Optional[str] = None


class TriagemCreate(TriagemBase):
    pass


class TriagemResponse(TriagemBase):
    id: int
    data_triagem: datetime

    model_config = ConfigDict(from_attributes=True)