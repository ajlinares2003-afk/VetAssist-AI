from sqlalchemy import Column, Integer, BigInteger, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from database.database import Base
from datetime import datetime

class Agendamento(Base):
    __tablename__ = "agendamentos"

    id = Column(Integer, primary_key=True, index=True)
    animal_id = Column(BigInteger, ForeignKey("animal.id"), nullable=False)
    usuario_id = Column(Integer, ForeignKey("usuario.id"), nullable=True)  # <--- Ajustado para "usuario.id"
    data_horario = Column(DateTime, nullable=False, index=True)
    tipo_servico = Column(String(50), default="Consulta")
    status = Column(String(30), default="AGENDADO")
    observacoes = Column(Text, nullable=True)
    data_criacao = Column(DateTime, default=datetime.now)

    animal = relationship("Animal")
    veterinario = relationship("Usuario")