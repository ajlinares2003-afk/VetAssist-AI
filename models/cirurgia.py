from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Table
from sqlalchemy.orm import relationship
from database.database import Base

equipe_cirurgica_associacao = Table(
    'equipe_cirurgica',
    Base.metadata,
    Column('procedimento_id', Integer, ForeignKey('procedimentos_cirurgicos.id', ondelete="CASCADE"), primary_key=True),
    Column('usuario_id', Integer, ForeignKey('usuario.id', ondelete="CASCADE"), primary_key=True)
)

# --- 1. PROCEDIMENTO CIRÚRGICO ---
class ProcedimentoCirurgico(Base):
    __tablename__ = "procedimentos_cirurgicos"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String, unique=True, index=True, nullable=True)
    consulta_id = Column(Integer, ForeignKey("consulta.id"), nullable=False)
    descricao_tecnica = Column(String, nullable=False)
    classificacao_asa = Column(String, nullable=False)
    status = Column(String, default="Aguardando Cirurgia", nullable=False)  # Adicionado campo de status
    data_hora_inicio = Column(DateTime, nullable=False)
    data_hora_fim = Column(DateTime, nullable=True)
    observacoes_pos_operatorias = Column(String, nullable=True)

    consulta = relationship("Consulta", backref="procedimentos")
    equipe = relationship("Usuario", secondary=equipe_cirurgica_associacao)
    ficha_anestesica = relationship("FichaAnestesica", back_populates="procedimento", uselist=False, cascade="all, delete-orphan")

    @property
    def animal_nome(self):
        """Propriedade para retornar diretamente o nome do animal vinculado à consulta."""
        if self.consulta and hasattr(self.consulta, 'animal') and self.consulta.animal:
            return self.consulta.animal.nome
        return "Paciente não identificado"


# --- 2. FICHA ANESTÉSICA ---
class FichaAnestesica(Base):
    __tablename__ = "fichas_anestesicas"

    id = Column(Integer, primary_key=True, index=True)
    procedimento_id = Column(Integer, ForeignKey("procedimentos_cirurgicos.id", ondelete="CASCADE"), nullable=False, unique=True)
    protocolo_mpa = Column(String, nullable=True)
    protocolo_inducao = Column(String, nullable=True)
    protocolo_manutencao = Column(String, nullable=True)
    fluidoterapia = Column(String, nullable=True)

    procedimento = relationship("ProcedimentoCirurgico", back_populates="ficha_anestesica")
    monitoramentos = relationship("MonitoramentoAnestesico", back_populates="ficha", cascade="all, delete-orphan")


# --- 3. MONITORAMENTO TRANSOPERATÓRIO ---
class MonitoramentoAnestesico(Base):
    __tablename__ = "monitoramentos_anestesicos"

    id = Column(Integer, primary_key=True, index=True)
    ficha_anestesica_id = Column(Integer, ForeignKey("fichas_anestesicas.id", ondelete="CASCADE"), nullable=False)
    tempo_minutos = Column(Integer, nullable=False)
    frequencia_cardiaca = Column(Integer, nullable=True)
    frequencia_respiratoria = Column(Integer, nullable=True)
    temperatura = Column(Float, nullable=True)
    spo2 = Column(Integer, nullable=True)
    pas = Column(Integer, nullable=True)
    pad = Column(Integer, nullable=True)

    ficha = relationship("FichaAnestesica", back_populates="monitoramentos")