from sqlalchemy import Column
from sqlalchemy import BigInteger
from sqlalchemy import Integer
from sqlalchemy import DateTime
from sqlalchemy import Text
from sqlalchemy import Numeric
from sqlalchemy import ForeignKey
from sqlalchemy.orm import relationship
from database.database import Base
from datetime import datetime
from sqlalchemy.sql import func

data_consulta = Column(
    DateTime,
    nullable=False,
    server_default=func.now()
)

class Consulta(Base):
    __tablename__ = "consulta"

    id = Column(
        BigInteger,
        primary_key=True,
        index=True
    )

    usuario_id = Column(
    BigInteger,
    ForeignKey("usuario.id")
    )

    animal_id = Column(
    BigInteger,
    ForeignKey("animal.id"),
    nullable=False
    )

    data_consulta = Column(
        DateTime,
        nullable=False,
        server_default=func.now()
    )

    queixa_principal = Column(Text)
    historico_clinico = Column(Text)
    sintomas = Column(Text)
    exame_fisico = Column(Text)
    peso_atendimento = Column(Numeric)
    temperatura = Column(Numeric)
    frequencia_cardiaca = Column(Integer)
    frequencia_respiratoria = Column(Integer)
    observacoes = Column(Text)

    animal = relationship(
        "Animal",
        back_populates="consultas"
    )

    exames = relationship(
        "Exame",
        back_populates="consulta",
        cascade="all, delete"
    )

    prescricoes = relationship(
        "Prescricao",
        back_populates="consulta",
        cascade="all, delete"
    )