from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class ConsultaBase(BaseModel):
    codigo: Optional[str] = None
    usuario_id: int
    animal_id: int
    status: Optional[str] = "CONCLUIDA"
    queixa_principal: str  # Campo obrigatório para validar o atendimento
    historico_clinico: Optional[str] = None
    sintomas: Optional[str] = None
    exame_fisico: Optional[str] = None
    peso_atendimento: Optional[float] = None
    temperatura: Optional[float] = None
    frequencia_cardiaca: Optional[int] = None
    frequencia_respiratoria: Optional[int] = None
    parecer_copiloto: Optional[str] = None  # Armazena a análise do Gemini
    observacoes: Optional[str] = None

class ConsultaCreate(ConsultaBase):
    pass

class ConsultaUpdate(BaseModel):
    codigo: Optional[str] = None
    usuario_id: Optional[int] = None
    animal_id: Optional[int] = None
    status: Optional[str] = None
    queixa_principal: Optional[str] = None
    historico_clinico: Optional[str] = None
    sintomas: Optional[str] = None
    exame_fisico: Optional[str] = None
    peso_atendimento: Optional[float] = None
    temperatura: Optional[float] = None
    frequencia_cardiaca: Optional[int] = None
    frequencia_respiratoria: Optional[int] = None
    parecer_copiloto: Optional[str] = None
    observacoes: Optional[str] = None

class ConsultaResponse(ConsultaBase):
    id: int
    data_consulta: datetime

    model_config = ConfigDict(from_attributes=True)