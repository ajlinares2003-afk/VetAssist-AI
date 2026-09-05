from sqlalchemy import Column, BigInteger, Integer, DateTime, Text, Numeric, String, ForeignKey
from sqlalchemy.orm import relationship
from database.database import Base
from sqlalchemy.sql import func

class Consulta(Base):
    __tablename__ = "consulta"

    id = Column(BigInteger, primary_key=True, index=True)
    codigo = Column(String(20), unique=True, index=True, nullable=True) # Padrão CNS-0001

    usuario_id = Column(BigInteger, ForeignKey("usuario.id"), nullable=False)
    animal_id = Column(BigInteger, ForeignKey("animal.id"), nullable=False)

    data_consulta = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    status = Column(String(30), default="CONCLUIDA", nullable=False) # AGENDADA, EM_ATENDIMENTO, CONCLUIDA, CANCELADA

    queixa_principal = Column(Text, nullable=True)
    historico_clinico = Column(Text, nullable=True)
    sintomas = Column(Text, nullable=True)
    exame_fisico = Column(Text, nullable=True)
    peso_atendimento = Column(Numeric(5, 2), nullable=True)
    temperatura = Column(Numeric(4, 1), nullable=True)
    frequencia_cardiaca = Column(Integer, nullable=True)
    frequencia_respiratoria = Column(Integer, nullable=True)
    parecer_copiloto = Column(Text, nullable=True)
    observacoes = Column(Text, nullable=True)

    # Relacionamentos
    animal = relationship("Animal", back_populates="consultas")
    exames = relationship("Exame", back_populates="consulta", cascade="all, delete")
    prescricoes = relationship("Prescricao", back_populates="consulta", cascade="all, delete")