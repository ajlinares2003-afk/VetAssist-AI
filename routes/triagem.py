from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database.database import get_db
from models.consulta import Consulta
from models.animais import Animal
from services.security import obter_usuario_logado
from pydantic import BaseModel
from typing import Optional

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

@router.post("/")
def criar_triagem(
    triagem: TriagemCreate,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    consulta = db.query(Consulta).filter(Consulta.id == triagem.consulta_id).first()
    if not consulta:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    
    consulta.status = "Aguardando Consulta (Fila Vet)"
    consulta.queixa_principal = triagem.queixa_principal
    consulta.temperatura = triagem.temperatura
    consulta.frequencia_cardiaca = triagem.frequencia_cardiaca
    consulta.frequencia_respiratoria = triagem.frequencia_respiratoria
    
    if triagem.peso is not None:
        if hasattr(consulta, 'peso_atendimento'):
            consulta.peso_atendimento = triagem.peso
        
        # Opcional: Atualiza também o peso oficial do animal se desejar
        animal = db.query(Animal).filter(Animal.id == consulta.animal_id).first()
        if animal and hasattr(animal, 'peso'):
            animal.peso = triagem.peso

    db.commit()
    return {"mensagem": "Triagem concluída com sucesso!"}