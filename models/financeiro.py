from sqlalchemy import Column, Integer, String, Float, Date
from database.database import Base

class TransacaoFinanceira(Base):
    __tablename__ = "transacoes_financeiras"

    id = Column(Integer, primary_key=True, index=True)
    tipo = Column(String, nullable=False)          # Receita ou Despesa
    categoria = Column(String, nullable=False)     # Consulta, Cirurgia, Exames, etc.
    descricao = Column(String, nullable=False)
    valor = Column(Float, nullable=False)
    forma_pagamento = Column(String, default="PIX")
    data = Column(Date, nullable=False)