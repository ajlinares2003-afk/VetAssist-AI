from sqlalchemy import Column
from sqlalchemy import BigInteger
from sqlalchemy import String
from sqlalchemy import Date
from sqlalchemy import Text
from sqlalchemy import ForeignKey
from sqlalchemy.orm import relationship
from database.database import Base

class Vacina(Base):
    __tablename__ = "vacina"

    id = Column(
        BigInteger,
        primary_key=True,
        index=True
    )

    animal_id = Column(
        BigInteger,
        ForeignKey("animal.id"),
        nullable=False
    )

    nome_vacina = Column(
        String(100),
        nullable=False
    )

    fabricante = Column(
        String(100)
    )

    dose = Column(
        String(50)
    )

    data_aplicacao = Column(
        Date,
        nullable=False
    )

    data_reforco = Column(
        Date
    )

    lote = Column(
        String(100)
    )

    observacoes = Column(
        Text
    )

    animal = relationship(
        "Animal",
        back_populates="vacinas"
    )