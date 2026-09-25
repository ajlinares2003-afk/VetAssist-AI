import enum
from sqlalchemy import Column, BigInteger, String, Boolean, TIMESTAMP
from sqlalchemy.sql import func
from database.database import Base

class PerfilUsuarioEnum(str, enum.Enum):
    ADMIN = "ADMIN"
    RECEPCAO = "RECEPCAO"
    TRIAGEM = "TRIAGEM"
    VETERINARIO = "VETERINARIO"

class Usuario(Base):
    __tablename__ = "usuario"

    id = Column(BigInteger, primary_key=True, index=True)
    nome = Column(String(150), nullable=False)
    email = Column(String(150), nullable=False, unique=True, index=True)
    senha_hash = Column(String(255), nullable=False)
    
    # Salva como String comum no banco para evitar conflitos com TYPE Enum do Postgres
    perfil = Column(String(50), default="VETERINARIO", nullable=False)
    
    crmv = Column(String(50), nullable=True)
    ativo = Column(Boolean, nullable=False, default=True)
    data_cadastro = Column(
        TIMESTAMP,
        server_default=func.now()
    )