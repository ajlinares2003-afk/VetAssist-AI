from sqlalchemy import Column, Integer, String, Boolean
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from backend.database import Base # Ajuste o caminho se sua Base ficar em outro lugar

class DoencaNotificacao(Base):
    """
    Tabela que armazena as regras determinísticas de bloqueio sanitário
    (baseado na IN MAPA 50/2013 e outras normativas oficiais).
    """
    __tablename__ = "defesa_sanitaria"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, unique=True, nullable=False, index=True)
    
    # Padrão Regex para detecção no texto (ex: \bencefalomielite\b)
    padrao = Column(String, nullable=False)
    
    # Lista de grupos afetados (ex: ["equideos", "ruminantes"])
    grupos = Column(ARRAY(String), nullable=False)
    
    # Texto descritivo geral sobre os exames
    exames = Column(String, nullable=True)
    
    # Lista de dicionários em JSONB detalhando exames sugeridos e justificativas
    exames_lista = Column(JSONB, nullable=True) 
    
    # Avisos de EPI e riscos (ex: Raiva, Carbúnculo)
    aviso = Column(String, nullable=True)
    
    # Restrições de tratamento (ex: proibir AINEs)
    tratamento = Column(String, nullable=True)
    
    # Lista de passos da conduta oficial (ex: isolamento, notificação)
    conduta = Column(JSONB, nullable=True) 
    
    # Regras de notificação (Imediata vs Mensal) e Carência
    notificacao_confirmada = Column(Boolean, default=False)
    sem_carencia = Column(Boolean, default=False)