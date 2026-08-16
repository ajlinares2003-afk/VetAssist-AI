from pydantic import BaseModel
from pydantic import ConfigDict
from typing import Optional
from datetime import date

class VacinaBase(BaseModel):
    animal_id: int
    nome_vacina: str
    fabricante: Optional[str] = None
    lote: Optional[str] = None
    dose: Optional[str] = None
    data_aplicacao: date
    data_reforco: Optional[date] = None
    observacoes: Optional[str] = None

class VacinaCreate(VacinaBase):
    pass

class VacinaUpdate(VacinaBase):
    pass

class VacinaResponse(VacinaBase):
    id: int
    model_config = ConfigDict(
        from_attributes=True
    )