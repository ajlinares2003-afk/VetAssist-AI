import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from database import engine, Base

# IMPORTAÇÃO DE TODOS OS MODELOS (mantida para o mapeamento do ORM; as tabelas
# são criadas manualmente no Supabase, pois o create_all está desativado)
from models.usuario import Usuario
from models.tutor import Tutor
from models.animais import Animal
from models.agendamento import Agendamento
from models.configuracao import ConfiguracaoSistema
from models.consulta import Consulta
from models.prontuario import ProntuarioSalvo
from models.vacina import Vacina
from models.exame import Exame
from models.prescricao import Prescricao, ItemPrescricao
from models.triagem import Triagem
from models.internacao import Internacao, EvolucaoInternacao
from models.financeiro import TransacaoFinanceira

from routes.usuarios import router as usuarios_router
from routes.tutores import router as tutores_router
from routes.animais import router as animais_router
from routes.agendamentos import router as agendamentos_router
from routes.consultas import router as consultas_router
from routes import cirurgias
from routes.exames import router as exames_router
from routes.vacinas import router as vacinas_router
from routes.prescricoes import router as prescricoes_router
from routes.prontuarios import router as prontuarios_router
from routes.dashboard import router as dashboard_router
from routes.auth import router as auth_router
from routes.internacoes import router as internacoes_router
from routes import triagem
from routes import financeiro
from routes import configuracoes
from routes import atendimento_ia
from services import rag_service
from services.rag_service import router as rag_router

logger = logging.getLogger("vetassist.startup")

app = FastAPI(
    title="VetAssist AI",
    version="0.1.0"
)


def _descrever_conexao(conn, rotulo: str) -> None:
    """Mostra a qual banco esta conexão realmente chegou (sem expor a senha)."""
    url = conn.engine.url
    banco, usuario = conn.execute(
        text("select current_database(), current_user")
    ).one()
    tabela_rag = conn.execute(
        text("select to_regclass('public.documentos_veterinarios') is not null")
    ).scalar()
    print(
        f"🔎 [{rotulo}] host={url.host} porta={url.port} banco={banco} "
        f"usuario={usuario} | tabela documentos_veterinarios: "
        f"{'OK' if tabela_rag else 'NÃO EXISTE'}"
    )


def verificar_conexoes() -> None:
    """
    Verificação REAL de conexão (a versão anterior imprimia sucesso sem conectar).
    Compara o engine do main.py com o engine usado pelas rotas do RAG: se os dois
    apontarem para bancos diferentes, o aviso aparece aqui, no arranque.
    """
    try:
        with engine.connect() as conn:
            _descrever_conexao(conn, "engine principal")
        print("✅ Conexão com o banco estabelecida com sucesso!")
    except Exception as e:
        print(f"⚠️ Falha ao conectar no banco principal: {e}")
        return

    # Conexão exatamente como a rota /rag/ingerir a obtém (via get_db do rag_service)
    obter_sessao = getattr(rag_service, "get_db", None)
    if obter_sessao is None:
        print("⚠️ rag_service não expõe get_db; verificação da sessão do RAG ignorada.")
        return
    gerador = obter_sessao()
    try:
        sessao = next(gerador)
        with sessao.get_bind().connect() as conn:
            _descrever_conexao(conn, "sessão do RAG")
    except Exception as e:
        print(f"⚠️ Falha ao verificar a sessão usada pelo RAG: {e}")
    finally:
        gerador.close()


verificar_conexoes()

# Ajustes opcionais de colunas, de forma isolada
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE consulta ALTER COLUMN frequencia_respiratoria DROP NOT NULL;"))
        conn.execute(text("ALTER TABLE consulta ALTER COLUMN frequencia_cardiaca DROP NOT NULL;"))
        conn.execute(text("ALTER TABLE consulta ALTER COLUMN temperatura DROP NOT NULL;"))
        conn.execute(text("ALTER TABLE consulta ALTER COLUMN peso_atendimento DROP NOT NULL;"))
        conn.execute(text("ALTER TABLE consulta ADD COLUMN IF NOT EXISTS parecer_copiloto TEXT;"))
        conn.commit()
        print("✅ Restrições de banco atualizadas e coluna parecer_copiloto verificada!")
    except Exception as e:
        conn.rollback()
        print(f"⚠️ Nota sobre atualizações de colunas: {e}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://vetassist-frontend-kappa.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(usuarios_router)
app.include_router(tutores_router)
app.include_router(animais_router)
app.include_router(agendamentos_router)
app.include_router(consultas_router)
app.include_router(exames_router)
app.include_router(vacinas_router)
app.include_router(prescricoes_router)
app.include_router(prontuarios_router)
app.include_router(dashboard_router)
app.include_router(auth_router)
app.include_router(triagem.router)
app.include_router(internacoes_router)
app.include_router(cirurgias.router)
app.include_router(financeiro.router)
app.include_router(configuracoes.router)
app.include_router(atendimento_ia.router)
app.include_router(rag_router)


@app.get("/")
def home():
    return {
        "mensagem": "VetAssist AI funcionando"
    }