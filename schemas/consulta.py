from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class ConsultaBase(BaseModel):
    codigo: Optional[str] = None
    usuario_id: Optional[int] = None
    animal_id: int
    status: Optional[str] = "AGUARDANDO_TRIAGEM"
    queixa_principal: Optional[str] = "Check-in de rotina / Recepção"
    historico_clinico: Optional[str] = None
    sintomas: Optional[str] = None
    exame_fisico: Optional[str] = None
    suspeita_diagnostica: Optional[str] = None  # Novo campo para a suspeita diagnóstica
    peso_atendimento: Optional[float] = None
    temperatura: Optional[float] = None
    frequencia_cardiaca: Optional[int] = None
    frequencia_respiratoria: Optional[int] = None
    parecer_copiloto: Optional[str] = None  # Armazena a análise do Gemini
    observacoes: Optional[str] = None
    indicacao_cirurgia: Optional[bool] = False
    justificativa_cirurgica: Optional[str] = None

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
    suspeita_diagnostica: Optional[str] = None 
    peso_atendimento: Optional[float] = None
    temperatura: Optional[float] = None
    frequencia_cardiaca: Optional[int] = None
    frequencia_respiratoria: Optional[int] = None
    parecer_copiloto: Optional[str] = None
    observacoes: Optional[str] = None
    indicacao_cirurgia: Optional[bool] = None
    justificativa_cirurgica: Optional[str] = None

class ConsultaResponse(ConsultaBase):
    id: int
    data_consulta: datetime

    model_config = ConfigDict(from_attributes=True)