from sqlalchemy import Column, BigInteger, String, DateTime, ForeignKey, Text, Float
from sqlalchemy.sql import func
from database.database import Base

class Internacao(Base):
    __tablename__ = "internacao"

    id = Column(BigInteger, primary_key=True, index=True)
    animal_id = Column(BigInteger, ForeignKey("animal.id"), nullable=False)
    consulta_id = Column(BigInteger, ForeignKey("consulta.id"), nullable=True)
    leito = Column(String(50), nullable=False)  # Ex: "Canil 01", "Gatil 01", "UTI 01"
    status = Column(String(20), default="INTERNADO")  # INTERNADO, ALTA, OBITO
    nivel_criticidade = Column(String(20), default="ESTAVEL")  # ESTAVEL, MODERADO, CRITICO
    motivo = Column(Text, nullable=False)
    data_entrada = Column(DateTime, server_default=func.now())
    data_alta = Column(DateTime, nullable=True)

class EvolucaoInternacao(Base):
    __tablename__ = "evolucao_internacao"

    id = Column(BigInteger, primary_key=True, index=True)
    internacao_id = Column(BigInteger, ForeignKey("internacao.id"), nullable=False)
    temperatura = Column(Float, nullable=True)
    freq_cardiaca = Column(Float, nullable=True)
    freq_respiratoria = Column(Float, nullable=True)
    tpc_segundos = Column(Float, nullable=True)
    mucosa = Column(String(50), nullable=True)
    alimentacao = Column(String(100), nullable=True)
    dejecoes = Column(String(100), nullable=True)
    observacoes = Column(Text, nullable=True)
    data_registro = Column(DateTime, server_default=func.now())