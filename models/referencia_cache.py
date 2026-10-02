from sqlalchemy import Column, BigInteger, Text, TIMESTAMP
from sqlalchemy.sql import func
from database.database import Base

class ReferenciaCache(Base):
    __tablename__ = "referencias_clinicas_cache"

    id = Column(BigInteger, primary_key=True, index=True)
    especie = Column(Text, nullable=False)
    sub_especie = Column(Text, nullable=True)
    raca = Column(Text, nullable=True)
    porte = Column(Text, nullable=True)
    sexo = Column(Text, nullable=True)          # Essencial para o dimorfismo sexual
    peso_ref = Column(Text, nullable=True)
    ecc_ref = Column(Text, nullable=True)
    temperatura = Column(Text, nullable=True)
    fc = Column(Text, nullable=True)
    fr = Column(Text, nullable=True)
    tpc = Column(Text, nullable=True)
    mucosas = Column(Text, nullable=True)
    fonte_ref = Column(Text, nullable=True)     # Rastreabilidade bibliográfica
    created_at = Column(TIMESTAMP, server_default=func.now(), nullable=False)