from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from datetime import datetime


# --- SCHEMAS DE ITEM DA PRESCRIÇÃO (MEDICAMENTO) ---

class ItemPrescricaoBase(BaseModel):
    medicamento: str
    dosagem: Optional[str] = None
    frequencia: Optional[str] = None
    duracao: Optional[str] = None
    tipo_uso: Optional[str] = None  # Ex: "Uso Veterinário", "Uso Humano", "Farmácia de Manipulação"
    observacoes: Optional[str] = None


class ItemPrescricaoCreate(ItemPrescricaoBase):
    pass


class ItemPrescricaoResponse(ItemPrescricaoBase):
    id: int
    prescricao_id: int

    model_config = ConfigDict(
        from_attributes=True
    )


# --- SCHEMAS DA PRESCRIÇÃO (RECEITA PAI) ---

class PrescricaoBase(BaseModel):
    consulta_id: int
    observacoes: Optional[str] = None


class PrescricaoCreate(PrescricaoBase):
    # Aceita uma lista de medicamentos na mesma requisição de criação
    itens: List[ItemPrescricaoCreate]


class PrescricaoUpdate(BaseModel):
    consulta_id: Optional[int] = None
    observacoes: Optional[str] = None
    itens: Optional[List[ItemPrescricaoCreate]] = None


class PrescricaoResponse(PrescricaoBase):
    id: int
    data_prescricao: datetime
    # Retorna todos os itens/medicamentos associados a esta receita
    itens: List[ItemPrescricaoResponse] = []

    model_config = ConfigDict(
        from_attributes=True
    )