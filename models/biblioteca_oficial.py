from sqlalchemy import Column, BigInteger, Text, Numeric, Integer, TIMESTAMP
from sqlalchemy.sql import func
from database.database import Base

class BibliotecaParametrosOficiais(Base):
    __tablename__ = "biblioteca_parametros_oficiais"

    id = Column(BigInteger, primary_key=True, index=True)
    classe_animal = Column(Text, nullable=False)
    grupo = Column(Text, nullable=False)
    especie = Column(Text, nullable=False)
    nome_cientifico = Column(Text, nullable=False)
    sexo = Column(Text, nullable=False, default='indiferente')
    
    peso_min = Column(Numeric(8,2), nullable=True)
    peso_max = Column(Numeric(8,2), nullable=True)
    
    temp_repouso_min = Column(Numeric(4,1), nullable=True)
    temp_repouso_max = Column(Numeric(4,1), nullable=True)
    temp_clinica_min = Column(Numeric(4,1), nullable=True)
    temp_clinica_max = Column(Numeric(4,1), nullable=True)
    
    fc_repouso_min = Column(Integer, nullable=True)
    fc_repouso_max = Column(Integer, nullable=True)
    fc_clinica_min = Column(Integer, nullable=True)
    fc_clinica_max = Column(Integer, nullable=True)
    
    fr_repouso_min = Column(Integer, nullable=True)
    fr_repouso_max = Column(Integer, nullable=True)
    fr_clinica_min = Column(Integer, nullable=True)
    fr_clinica_max = Column(Integer, nullable=True)
    
    tpc_ref = Column(Text, nullable=True)
    mucosas_ref = Column(Text, nullable=True)
    ecc_ideal = Column(Text, nullable=True)
    fonte_bibliografica = Column(Text, nullable=False)
    
    created_at = Column(TIMESTAMP, server_default=func.now(), nullable=False)