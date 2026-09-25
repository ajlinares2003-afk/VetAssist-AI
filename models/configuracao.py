from sqlalchemy import Column, Integer, String

# Importação segura do Base do projeto, prevenindo erros de nome
try:
    from database import Base
except ImportError:
    try:
        from database import DeclarativeBase as Base
    except ImportError:
        from sqlalchemy.orm import declarative_base
        Base = declarative_base()

class ConfiguracaoSistema(Base):
    __tablename__ = "configuracoes_sistema"

    id = Column(Integer, primary_key=True, index=True)
    chave = Column(String(50), unique=True, index=True)
    valor = Column(String(255), nullable=False)