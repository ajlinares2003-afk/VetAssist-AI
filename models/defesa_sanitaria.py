"""Modelo da tabela public.defesa_sanitaria (já existente no Supabase). Salve em models/defesa_sanitaria.py."""
from sqlalchemy import Column, Integer, String, Boolean, JSON
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy import Text
from database import Base

# with_variant: no PostgreSQL usa ARRAY/JSONB; em SQLite (testes) cai para JSON. Não muda nada em produção.
_ARRAY_TEXT = ARRAY(Text).with_variant(JSON(), "sqlite")
_JSONB = JSONB().with_variant(JSON(), "sqlite")


class DoencaNotificacao(Base):
    __tablename__ = "defesa_sanitaria"

    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False)
    padrao = Column(String, nullable=False)          # regex, minúsculo e SEM acento (aplicado ao texto normalizado)
    grupos = Column(_ARRAY_TEXT, nullable=False)     # ex.: {"equideos"}; vazio = vale para qualquer espécie
    exames = Column(String)
    exames_lista = Column(_JSONB)                    # [{"nome":..., "justificativa":...}]
    aviso = Column(String)
    tratamento = Column(String)
    conduta = Column(_JSONB)                         # ["Isolar...", "Notificar..."]
    notificacao_confirmada = Column(Boolean, default=False)
    sem_carencia = Column(Boolean, default=False)
    exclusiva = Column(Boolean, nullable=False, default=False)  # NOVA (rodar o .sql)
    sinais = Column(_ARRAY_TEXT)                                # NOVA (rodar o .sql)