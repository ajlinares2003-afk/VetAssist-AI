from sqlalchemy import Column
from sqlalchemy import BigInteger
from sqlalchemy import String
from sqlalchemy import TIMESTAMP
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from database.database import Base

class Tutor(Base):
    __tablename__ = "tutor"

    id = Column(BigInteger, primary_key=True, index=True)
    codigo = Column(String(20), unique=True, index=True, nullable=True) # <- NOVO CAMPO
    nome = Column(String(150), nullable=False)
    cpf = Column(String(14), nullable=False)
    telefone = Column(String(20), nullable=False)
    email = Column(String(150))

    animais = relationship(
        "Animal",
        back_populates="tutor"
    )
    
    data_cadastro = Column(
        TIMESTAMP,
        server_default=func.now()
    )