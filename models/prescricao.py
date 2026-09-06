from sqlalchemy import Column, BigInteger, Text, TIMESTAMP, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from database.database import Base


class Prescricao(Base):
    """Representa a Receita Médica (Cabeçalho vinculado à Consulta)."""
    __tablename__ = "prescricao"

    id = Column(
        BigInteger,
        primary_key=True,
        index=True
    )

    consulta_id = Column(
        BigInteger,
        ForeignKey("consulta.id"),
        nullable=False
    )

    observacoes = Column(
        Text,
        nullable=True
    )

    data_prescricao = Column(
        TIMESTAMP,
        server_default=func.now(),
        nullable=False
    )

    # Relacionamento com a Consulta
    consulta = relationship(
        "Consulta",
        back_populates="prescricoes"
    )

    # Relacionamento 1 para N com os itens/medicamentos prescritos
    itens = relationship(
        "ItemPrescricao",
        back_populates="prescricao",
        cascade="all, delete-orphan"
    )


class ItemPrescricao(Base):
    """Representa cada medicamento individual dentro de uma receita."""
    __tablename__ = "item_prescricao"

    id = Column(
        BigInteger,
        primary_key=True,
        index=True
    )

    prescricao_id = Column(
        BigInteger,
        ForeignKey("prescricao.id"),
        nullable=False
    )

    # Suporta textos extensos e orientações completas da IA
    medicamento = Column(
        Text,
        nullable=False
    )

    dosagem = Column(
        Text,
        nullable=True
    )

    frequencia = Column(
        Text,
        nullable=True
    )

    duracao = Column(
        Text,
        nullable=True
    )

    # Campo para indicar local de aquisição/orientação ao tutor
    tipo_uso = Column(
        Text,
        nullable=True
    )

    observacoes = Column(
        Text,
        nullable=True
    )

    # Relacionamento com a Prescrição pai
    prescricao = relationship(
        "Prescricao",
        back_populates="itens"
    )