from sqlalchemy import Column
from sqlalchemy import BigInteger
from sqlalchemy import String
from sqlalchemy import Boolean
from sqlalchemy import TIMESTAMP
from sqlalchemy.sql import func
from database.database import Base
class Usuario(Base):
    __tablename__ = "usuario"
    id = Column(BigInteger, primary_key=True, index=True)
    nome = Column(String(150), nullable=False)
    email = Column(String(150), nullable=False)
    senha_hash = Column(String(255), nullable=False)
    perfil = Column(String(50), nullable=False)
    ativo = Column(Boolean, nullable=False, default=True)
    data_cadastro = Column(
        TIMESTAMP,
        server_default=func.now()
    )