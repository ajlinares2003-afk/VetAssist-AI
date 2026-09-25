from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import date

from database.database import get_db
from models.financeiro import TransacaoFinanceira

router = APIRouter(prefix="/financeiro", tags=["Financeiro & Caixa"])

class TransacaoCreate(BaseModel):
    tipo: str
    categoria: str
    descricao: str
    valor: float
    forma_pagamento: Optional[str] = "PIX"
    data: date

@router.get("/")
def listar_transacoes(db: Session = Depends(get_db)):
    transacoes = db.query(TransacaoFinanceira).order_by(TransacaoFinanceira.id.desc()).all()
    return transacoes

@router.post("/", status_code=status.HTTP_201_CREATED)
def criar_transacao(transacao: TransacaoCreate, db: Session = Depends(get_db)):
    nova_transacao = TransacaoFinanceira(
        tipo=transacao.tipo,
        categoria=transacao.categoria,
        descricao=transacao.descricao,
        valor=transacao.valor,
        forma_pagamento=transacao.forma_pagamento,
        data=transacao.data
    )
    db.add(nova_transacao)
    db.commit()
    db.refresh(nova_transacao)
    return nova_transacao