from pydantic import BaseModel
from pydantic import ConfigDict
from typing import Optional
from datetime import date

class ExameBase(BaseModel):
    consulta_id: int
    tipo_exame: str
    nome_exame: str
    data_exame: date
    resultado: Optional[str] = None
    arquivo: Optional[str] = None
    observacoes: Optional[str] = None

class ExameCreate(ExameBase):
    pass

class ExameUpdate(ExameBase):
    pass

class ExameResponse(ExameBase):
    id: int
    model_config = ConfigDict(
        from_attributes=True
    )