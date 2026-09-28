from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database.database import get_db
from models.consulta import Consulta
from models.animais import Animal
from services.security import obter_usuario_logado
from pydantic import BaseModel
from typing import Optional
from models.triagem import Triagem 

router = APIRouter(
    prefix="/triagem",
    tags=["Triagem"]
)

class TriagemCreate(BaseModel):
    consulta_id: int
    peso: Optional[float] = None
    temperatura: Optional[float] = None
    frequencia_cardiaca: Optional[int] = None
    frequencia_respiratoria: Optional[int] = None
    tpc_segundos: Optional[int] = None
    mucosas: Optional[str] = "Normocoradas"
    desidratacao_percentual: Optional[int] = None
    queixa_principal: str
    classificacao_risco: str
    justificativa_risco: Optional[str] = None

@router.post("/", status_code=status.HTTP_201_CREATED)
def criar_ou_atualizar_triagem(
    dados: TriagemCreate,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    # Verifica se já existe triagem para esta consulta
    triagem_db = db.query(Triagem).filter(Triagem.consulta_id == dados.consulta_id).first()
    
    if triagem_db:
        # Atualiza a triagem existente
        triagem_db.peso = dados.peso
        triagem_db.temperatura = dados.temperatura
        triagem_db.frequencia_cardiaca = dados.frequencia_cardiaca
        triagem_db.frequencia_respiratoria = dados.frequencia_respiratoria
        triagem_db.tpc_segundos = dados.tpc_segundos
        triagem_db.mucosas = dados.mucosas
        triagem_db.desidratacao_percentual = dados.desidratacao_percentual
        triagem_db.queixa_principal = dados.queixa_principal
        triagem_db.classificacao_risco = dados.classificacao_risco
        triagem_db.justificativa_risco = dados.justificativa_risco
    else:
        # Cria uma nova triagem
        triagem_db = Triagem(
            consulta_id=dados.consulta_id,
            peso=dados.peso,
            temperatura=dados.temperatura,
            frequencia_cardiaca=dados.frequencia_cardiaca,
            frequencia_respiratoria=dados.frequencia_respiratoria,
            tpc_segundos=dados.tpc_segundos,
            mucosas=dados.mucosas,
            desidratacao_percentual=dados.desidratacao_percentual,
            queixa_principal=dados.queixa_principal,
            classificacao_risco=dados.classificacao_risco,
            justificativa_risco=dados.justificativa_risco
        )
        db.add(triagem_db)

    # Atualiza também o status da consulta para encaminhar para a fila do veterinário
    consulta = db.query(Consulta).filter(Consulta.id == dados.consulta_id).first()
    if consulta:
        consulta.status = "Aguardando Consulta (Fila Vet)"
        if dados.peso:
            consulta.peso_atendimento = dados.peso
        if dados.temperatura:
            consulta.temperatura = dados.temperatura
        if dados.frequencia_cardiaca:
            consulta.frequencia_cardiaca = dados.frequencia_cardiaca
        if dados.frequencia_respiratoria:
            consulta.frequencia_respiratoria = dados.frequencia_respiratoria

    db.commit()
    db.refresh(triagem_db)
    return {"mensagem": "Triagem guardada com sucesso!", "id": triagem_db.id}

@router.get("/fila-triagem")
def listar_fila_triagem(
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    consultas_aguardando = db.query(Consulta).filter(
        Consulta.status.in_([
            "AGUARDANDO_TRIAGEM",
            "Aguardando Triagem (Recepção)",
            "Aguardando Triagem",
            "Chamando para Triagem",
            "AGUARDANDO_VACINA",
            "Aguardando Vacina"
        ])
    ).order_by(Consulta.id.asc()).all()

    resultado = []
    for c in consultas_aguardando:
        animal = db.query(Animal).filter(Animal.id == c.animal_id).first()
        resultado.append({
            "id": c.id,
            "codigo": c.codigo or f"CNS-{c.id:04d}",
            "animal_id": c.animal_id,
            "pet": animal.nome if animal else "Paciente",
            "especie": animal.especie if animal else "-",
            "queixa_principal": c.queixa_principal,
            "peso_atendimento": getattr(c, 'peso_atendimento', None),
            "temperatura": c.temperatura,
            "frequencia_cardiaca": c.frequencia_cardiaca,
            "frequencia_respiratoria": c.frequencia_respiratoria,
            "status": c.status
        })
    return resultado

from models.triagem import Triagem  # Certifique-se de que o model Triagem está importado no topo do ficheiro

@router.get("/consulta/{consulta_id}")
def buscar_triagem_por_consulta(
    consulta_id: int,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    # 1. Procura primeiro na tabela dedicada 'triagem' (onde o Supabase guarda os dados)
    triagem_db = db.query(Triagem).filter(Triagem.consulta_id == consulta_id).first()
    
    if triagem_db:
        return {
            "consulta_id": triagem_db.consulta_id,
            "peso": triagem_db.peso,
            "temperatura": triagem_db.temperatura,
            "frequencia_cardiaca": triagem_db.frequencia_cardiaca,
            "frequencia_respiratoria": triagem_db.frequencia_respiratoria,
            "tpc_segundos": triagem_db.tpc_segundos,
            "mucosas": triagem_db.mucosas,
            "queixa_principal": triagem_db.queixa_principal,
            "observacoes": getattr(triagem_db, 'observacoes', "")
        }

    # 2. Fallback caso os dados estejam diretamente na tabela 'Consulta'
    consulta = db.query(Consulta).filter(Consulta.id == consulta_id).first()
    if not consulta:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    
    return {
        "consulta_id": consulta.id,
        "peso": getattr(consulta, 'peso_atendimento', None),
        "temperatura": consulta.temperatura,
        "frequencia_cardiaca": consulta.frequencia_cardiaca,
        "frequencia_respiratoria": consulta.frequencia_respiratoria,
        "tpc_segundos": getattr(consulta, 'tpc_segundos', 2),
        "mucosas": getattr(consulta, 'mucosas', "Normocoradas"),
        "observacoes": consulta.observacoes or ""
    }