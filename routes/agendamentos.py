from typing import Optional
from datetime import datetime
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from database.database import get_db
from models.agendamento import Agendamento
from models.consulta import Consulta
from models.animais import Animal
from models.usuario import Usuario
from services.security import obter_usuario_logado

router = APIRouter(prefix="/agendamentos", tags=["Agenda de Atendimentos"])

class AgendamentoCreate(BaseModel):
    animal_id: int
    usuario_id: Optional[int] = None
    data_horario: datetime
    tipo_servico: Optional[str] = "Consulta"
    observacoes: Optional[str] = None

@router.get("/")
def listar_agendamentos(
    data_consulta: Optional[str] = None,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    query = db.query(Agendamento)
    
    if data_consulta:
        data_target = datetime.strptime(data_consulta, "%Y-%m-%d").date()
        query = query.filter(func.date(Agendamento.data_horario) == data_target)
    
    agendamentos = query.order_by(Agendamento.data_horario.asc()).all()

    resultado = []
    for a in agendamentos:
        animal = db.query(Animal).filter(Animal.id == a.animal_id).first()
        vet = db.query(Usuario).filter(Usuario.id == a.usuario_id).first() if a.usuario_id else None
        
        nome_tutor = "-"
        if animal:
            if hasattr(animal, 'tutor') and animal.tutor:
                nome_tutor = animal.tutor.nome
            elif hasattr(animal, 'tutor_nome') and animal.tutor_nome:
                nome_tutor = animal.tutor_nome

        resultado.append({
            "id": a.id,
            "animal_id": a.animal_id,
            "pet_nome": animal.nome if animal else "Paciente",
            "especie": animal.especie if animal else "-",
            "tutor_nome": nome_tutor,
            "veterinario_id": a.usuario_id,
            "veterinario_nome": vet.nome if vet else "Qualquer Veterinário",
            "data_horario": a.data_horario.strftime("%Y-%m-%d %H:%M"),
            "hora": a.data_horario.strftime("%H:%M"),
            "tipo_servico": a.tipo_servico,
            "status": a.status,
            "observacoes": a.observacoes
        })
    return resultado

@router.post("/")
def criar_agendamento(
    dados: AgendamentoCreate,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    novo = Agendamento(
        animal_id=dados.animal_id,
        usuario_id=dados.usuario_id,
        data_horario=dados.data_horario,
        tipo_servico=dados.tipo_servico or "Consulta",
        status="AGENDADO",
        observacoes=dados.observacoes
    )
    db.add(novo)
    db.commit()
    db.refresh(novo)
    return {"mensagem": "Agendamento criado com sucesso!", "id": novo.id}

@router.put("/{agendamento_id}/status")
def atualizar_status(
    agendamento_id: int,
    novo_status: str,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    agendamento = db.query(Agendamento).filter(Agendamento.id == agendamento_id).first()
    if not agendamento:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado.")
    
    agendamento.status = novo_status

    if novo_status == "EM_ESPERA":
        nova_consulta = Consulta(
            animal_id=agendamento.animal_id,
            usuario_id=agendamento.usuario_id,
            queixa_principal=f"Agendado: {agendamento.tipo_servico} - {agendamento.observacoes or ''}",
            status="Aguardando Triagem (Recepção)",
            data_consulta=datetime.now()
        )
        db.add(nova_consulta)

    db.commit()
    return {"mensagem": f"Status atualizado para {novo_status}"}