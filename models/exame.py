from sqlalchemy import Column
from sqlalchemy import BigInteger
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import Date
from sqlalchemy import ForeignKey
from sqlalchemy.orm import relationship
from database.database import Base

class Exame(Base):
    __tablename__ = "exame"

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

    tipo_exame = Column(
        String(50),
        nullable=False
    )

    nome_exame = Column(
        String(150),
        nullable=False
    )

    data_exame = Column(
        Date,
        nullable=False
    )

    resultado = Column(
        Text
    )

    arquivo = Column(
        String(255)
    )

    observacoes = Column(
        Text
    )

    consulta = relationship(
        "Consulta",
        back_populates="exames"
    )