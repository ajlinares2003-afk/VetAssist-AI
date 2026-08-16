from sqlalchemy import Column
from sqlalchemy import BigInteger
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import TIMESTAMP
from sqlalchemy import ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from database.database import Base

class Prescricao(Base):
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

    medicamento = Column(
        String(150),
        nullable=False
    )

    dosagem = Column(
        String(100)
    )

    frequencia = Column(
        String(100)
    )

    duracao = Column(
        String(100)
    )

    observacoes = Column(
        Text
    )

    data_prescricao = Column(
        TIMESTAMP,
        server_default=func.now(),
        nullable=False
    )

    consulta = relationship(
        "Consulta",
        back_populates="prescricoes"
    )