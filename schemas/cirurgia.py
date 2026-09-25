from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional
from datetime import datetime
from enum import Enum

class ClassificacaoASAEnum(str, Enum):
    ASA_I = "ASA I - Paciente Saudável"
    ASA_II = "ASA II - Doença Sistêmica Leve"
    ASA_III = "ASA III - Doença Sistêmica Grave"
    ASA_IV = "ASA IV - Risco de Vida Constante"
    ASA_V = "ASA V - Moribundo"

# --- 1. PROCEDIMENTO CIRÚRGICO ---
class CirurgiaBase(BaseModel):
    codigo: Optional[str] = None
    consulta_id: int
    descricao_tecnica: str = Field(..., description="Descrição do procedimento")
    classificacao_asa: str
    status: Optional[str] = "Aguardando Cirurgia"  # Adicionado campo de status cirúrgico
    data_hora_inicio: datetime
    data_hora_fim: Optional[datetime] = None
    observacoes_pos_operatorias: Optional[str] = None

class CirurgiaCreate(CirurgiaBase):
    equipe_cirurgica_ids: List[int]

class CirurgiaUpdate(BaseModel):
    descricao_tecnica: Optional[str] = None
    classificacao_asa: Optional[str] = None
    status: Optional[str] = None
    data_hora_inicio: Optional[datetime] = None
    data_hora_fim: Optional[datetime] = None
    observacoes_pos_operatorias: Optional[str] = None

class CirurgiaResponse(CirurgiaBase):
    id: int
    animal_nome: Optional[str] = None  # Nome do paciente para exibição na tela
    model_config = ConfigDict(from_attributes=True)

# --- 2. FICHA ANESTÉSICA ---
class FichaAnestesicaBase(BaseModel):
    cirurgia_id: int
    protocolo_mpa: Optional[str] = None
    protocolo_inducao: Optional[str] = None
    protocolo_manutencao: Optional[str] = None
    fluidoterapia: Optional[str] = None

class FichaAnestesicaCreate(FichaAnestesicaBase):
    pass

class FichaAnestesicaResponse(FichaAnestesicaBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

# --- 3. MONITORAMENTO TRANSOPERATÓRIO ---
class MonitoramentoBase(BaseModel):
    ficha_anestesica_id: int
    tempo_minutos: int
    frequencia_cardiaca: Optional[int] = None
    frequencia_respiratoria: Optional[int] = None
    temperatura: Optional[float] = None
    spo2: Optional[int] = Field(None, le=100)
    pas: Optional[int] = None
    pad: Optional[int] = None

class MonitoramentoCreate(MonitoramentoBase):
    pass

class MonitoramentoResponse(MonitoramentoBase):
    id: int
    model_config = ConfigDict(from_attributes=True)