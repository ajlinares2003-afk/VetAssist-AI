import enum
from sqlalchemy import Column, BigInteger, Text, Numeric, Integer, TIMESTAMP, ForeignKey, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from database.database import Base


class StatusAtendimentoEnum(str, enum.Enum):
    AGUARDANDO_TRIAGEM = "AGUARDANDO_TRIAGEM"
    AGUARDANDO_CONSULTA = "AGUARDANDO_CONSULTA"
    EM_ATENDIMENTO = "EM_ATENDIMENTO"
    FINALIZADO = "FINALIZADO"
    CANCELADO = "CANCELADO"


class ClassificacaoRiscoEnum(str, enum.Enum):
    VERMELHO = "VERMELHO"  # Emergência
    LARANJA = "LARANJA"    # Muito Urgente
    AMARELO = "AMARELO"    # Urgente
    VERDE = "VERDE"        # Pouco Urgente
    AZUL = "AZUL"          # Não Urgente


class Triagem(Base):
    """Representa a Triagem / Pré-Atendimento do Paciente."""
    __tablename__ = "triagem"

    id = Column(BigInteger, primary_key=True, index=True)
    consulta_id = Column(BigInteger, ForeignKey("consulta.id"), nullable=False, unique=True)
    
    peso = Column(Numeric(5, 2), nullable=True)
    temperatura = Column(Numeric(4, 1), nullable=True)
    frequencia_cardiaca = Column(Integer, nullable=True)
    frequencia_respiratoria = Column(Integer, nullable=True)
    tpc_segundos = Column(Integer, nullable=True)
    desidratacao_percentual = Column(Integer, nullable=True)
    mucosas = Column(Text, nullable=True) 
    
    queixa_principal = Column(Text, nullable=False)
    classificacao_risco = Column(Enum(ClassificacaoRiscoEnum), nullable=False)
    justificativa_risco = Column(Text, nullable=True)
    
    data_triagem = Column(TIMESTAMP, server_default=func.now(), nullable=False)

    consulta = relationship("Consulta", back_populates="triagem")