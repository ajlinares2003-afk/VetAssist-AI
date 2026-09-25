from sqlalchemy import Column, Integer, String, Text, DateTime
from datetime import datetime
from database.database import Base

class ProntuarioSalvo(Base):
    __tablename__ = "prontuarios_salvos"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String, unique=True, index=True)
    animal_id = Column(Integer, index=True, nullable=True)  # Armazena o ID do animal sem travas de foreign key
    resumo_ia = Column(Text, nullable=True)
    data_criacao = Column(DateTime, default=datetime.utcnow)