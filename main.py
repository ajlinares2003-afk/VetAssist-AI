from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes.usuarios import router as usuarios_router
from routes.tutores import router as tutores_router
from routes.animais import router as animais_router
from routes.consultas import router as consultas_router
from routes.exames import router as exames_router
from routes.vacinas import router as vacinas_router
from routes.prescricoes import router as prescricoes_router
from routes.prontuarios import router as prontuarios_router
from routes.dashboard import router as dashboard_router
from routes.auth import router as auth_router

app = FastAPI(
    title="VetAssist AI",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(usuarios_router)
app.include_router(tutores_router)
app.include_router(animais_router)
app.include_router(consultas_router)
app.include_router(exames_router)
app.include_router(vacinas_router)
app.include_router(prescricoes_router)
app.include_router(prontuarios_router)
app.include_router(dashboard_router)
app.include_router(auth_router)

@app.get("/")
def home():
    return {
        "mensagem": "VetAssist AI funcionando"
    }