from sqlalchemy import Column
from sqlalchemy import BigInteger
from sqlalchemy import String
from sqlalchemy import DECIMAL
from sqlalchemy import TIMESTAMP
from sqlalchemy import Date
from sqlalchemy import ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from database.database import Base

class Animal(Base):
    __tablename__ = "animal"

    id = Column(BigInteger, primary_key=True, index=True)
    
    # Campo para o código único do cadastro (ex: PET-0001)
    codigo = Column(String(20), unique=True, index=True, nullable=True)

    nome = Column(String(100), nullable=False)
    especie = Column(String(50), nullable=False)
    sub_especie = Column(String(100), nullable=True)  # Novo campo adicionado para a subespécie
    raca = Column(String(100))
    sexo = Column(String(20), nullable=False)

    idade = Column(DECIMAL(4, 1))
    peso = Column(DECIMAL(5, 2))

    tutor_id = Column(
        BigInteger,
        ForeignKey("tutor.id"),
        nullable=False
    )

    tutor = relationship(
        "Tutor",
        back_populates="animais"
    )

    consultas = relationship(
        "Consulta",
        back_populates="animal",
        cascade="all, delete"
    )

    data_cadastro = Column(
        TIMESTAMP,
        server_default=func.now()
    )

    vacinas = relationship(
        "Vacina",
        back_populates="animal",
        cascade="all, delete"
    )
    
    data_nascimento = Column(Date)
    cor = Column(String(50))     # Ajustado para 50 caracteres
    microchip = Column(String(100))
    
    # Novos campos integrados corretamente
    castrado = Column(String(3), nullable=True)
    porte = Column(String(20), nullable=True)

    status = Column(
        String(50),
        nullable=False,
        default="ATIVO"
    )