from pydantic import BaseModel
from pydantic import ConfigDict
from typing import Optional
from datetime import datetime

class PrescricaoBase(BaseModel):
    consulta_id: int
    medicamento: str
    dosagem: Optional[str] = None
    frequencia: Optional[str] = None
    duracao: Optional[str] = None
    observacoes: Optional[str] = None

class PrescricaoCreate(PrescricaoBase):
    pass

class PrescricaoUpdate(PrescricaoBase):
    pass

class PrescricaoResponse(PrescricaoBase):
    id: int
    data_prescricao: datetime
    model_config = ConfigDict(
        from_attributes=True
    )