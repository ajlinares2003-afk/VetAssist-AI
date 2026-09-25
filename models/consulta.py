import enum
from sqlalchemy import Column, BigInteger, Integer, DateTime, Text, Numeric, String, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from database.database import Base
from sqlalchemy.sql import func


class StatusAtendimentoEnum(str, enum.Enum):
    AGUARDANDO_TRIAGEM = "AGUARDANDO_TRIAGEM"
    AGUARDANDO_CONSULTA = "AGUARDANDO_CONSULTA"
    EM_ATENDIMENTO = "EM_ATENDIMENTO"
    FINALIZADO = "FINALIZADO"
    CANCELADO = "CANCELADO"
    # Status cirúrgicos e legados
    AGUARDANDO_CIRURGIA = "Aguardando Cirurgia"
    EM_CIRURGIA = "Em Cirurgia"
    CONCLUIDA = "Concluída"
    AGENDADA = "AGUARDANDO_TRIAGEM"


class Consulta(Base):
    __tablename__ = "consulta"

    id = Column(BigInteger, primary_key=True, index=True)
    codigo = Column(String(20), unique=True, index=True, nullable=True)  # Padrão CNS-0001

    usuario_id = Column(BigInteger, ForeignKey("usuario.id"), nullable=False)
    animal_id = Column(BigInteger, ForeignKey("animal.id"), nullable=False)

    data_consulta = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    
    # Armazenado como String livre para aceitar qualquer status sem travar o banco
    status = Column(
        String(50),
        default="AGUARDANDO_TRIAGEM",
        nullable=False
    )

    queixa_principal = Column(Text, nullable=True)
    historico_clinico = Column(Text, nullable=True)
    sintomas = Column(Text, nullable=True)
    exame_fisico = Column(Text, nullable=True)
    suspeita_diagnostica = Column(Text, nullable=True)  # Campo para a suspeita diagnóstica gerada pela IA
    peso_atendimento = Column(Numeric(5, 2), nullable=True)
    temperatura = Column(Numeric(4, 1), nullable=True)
    frequencia_cardiaca = Column(Integer, nullable=True)
    frequencia_respiratoria = Column(Integer, nullable=True)
    parecer_copiloto = Column(Text, nullable=True)
    observacoes = Column(Text, nullable=True)
    indicacao_cirurgia = Column(Boolean, default=False, nullable=True)
    justificativa_cirurgica = Column(Text, nullable=True)

    # Relacionamentos
    animal = relationship("Animal", back_populates="consultas")
    exames = relationship("Exame", back_populates="consulta", cascade="all, delete")
    prescricoes = relationship("Prescricao", back_populates="consulta", cascade="all, delete")
    
    # Novo Relacionamento 1 para 1 com a Triagem (Pré-Atendimento)
    triagem = relationship(
        "Triagem",
        back_populates="consulta",
        uselist=False,
        cascade="all, delete-orphan"
    )