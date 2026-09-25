from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from database.database import engine, Base

# 1. IMPORTA PRIMEIRO OS MODELOS BASE NECESSÁRIOS (ANIMAL)
from models.animais import Animal
from models.usuario import Usuario
from models.agendamento import Agendamento
from models.configuracao import Base

# 2. DEPOIS IMPORTA O MODELO DEPENDENTE
from models.prontuario import ProntuarioSalvo

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

app = FastAPI(
    title="VetAssist AI",
    version="0.1.0"
)

# Cria as tabelas que ainda não existem no banco de dados
Base.metadata.create_all(bind=engine)

# Remove a restrição NOT NULL das colunas vitais e adiciona a coluna parecer_copiloto
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
        print(f"⚠️ Nota sobre atualizações de banco: {e}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
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

@app.get("/")
def home():
    return {
        "mensagem": "VetAssist AI funcionando"
    }