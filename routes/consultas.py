from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session
from database.database import get_db
from models.consulta import Consulta
from models.animais import Animal
from schemas.consulta import ConsultaCreate
from datetime import datetime
from services.security import (
    obter_usuario_logado,
    exigir_perfil
)

router = APIRouter(
    prefix="/consultas",
    tags=["Consultas"]
)

@router.get("/")
def listar_consultas(
    db: Session = Depends(get_db)
):
    consultas = db.query(Consulta).all()
    return consultas

@router.post("/")
def criar_consulta(
    consulta: ConsultaCreate,
    usuario_logado = Depends(
        exigir_perfil(
            ["ADMIN", "VETERINARIO"]
        )
    ),
    db: Session = Depends(get_db)
):
    animal = (
        db.query(Animal)
        .filter(Animal.id == consulta.animal_id)
        .first()
    )

    if not animal:
        return {
            "erro": "Animal nao encontrado"
        }

    nova_consulta = Consulta(
        usuario_id=consulta.usuario_id,
        animal_id=consulta.animal_id,
        queixa_principal=consulta.queixa_principal,
        historico_clinico=consulta.historico_clinico,
        sintomas=consulta.sintomas,
        exame_fisico=consulta.exame_fisico,
        peso_atendimento=consulta.peso_atendimento,
        temperatura=consulta.temperatura,
        frequencia_cardiaca=consulta.frequencia_cardiaca,
        frequencia_respiratoria=consulta.frequencia_respiratoria,
        observacoes=consulta.observacoes
    )

    db.add(nova_consulta)
    db.commit()
    db.refresh(nova_consulta)

    return nova_consulta

@router.put("/{consulta_id}")
def atualizar_consulta(
    consulta_id: int,
    consulta: ConsultaCreate,
    usuario_logado = Depends(
        exigir_perfil(
            ["ADMIN", "VETERINARIO"]
        )
    ),
    db: Session = Depends(get_db)
):

    consulta_db = (
        db.query(Consulta)
        .filter(Consulta.id == consulta_id)
        .first()
    )

    if not consulta_db:
        return {
            "erro": "Consulta nao encontrada"
        }

    consulta_db.usuario_id = consulta.usuario_id
    consulta_db.animal_id = consulta.animal_id
    consulta_db.queixa_principal = consulta.queixa_principal
    consulta_db.historico_clinico = consulta.historico_clinico
    consulta_db.sintomas = consulta.sintomas
    consulta_db.exame_fisico = consulta.exame_fisico
    consulta_db.peso_atendimento = consulta.peso_atendimento
    consulta_db.temperatura = consulta.temperatura
    consulta_db.frequencia_cardiaca = consulta.frequencia_cardiaca
    consulta_db.frequencia_respiratoria = consulta.frequencia_respiratoria
    consulta_db.observacoes = consulta.observacoes

    db.commit()
    db.refresh(consulta_db)

    return consulta_db

@router.delete("/{consulta_id}")
def excluir_consulta(
    consulta_id: int,
    usuario_logado = Depends(
        exigir_perfil(
            ["ADMIN", "VETERINARIO"]
        )
    ),
    db: Session = Depends(get_db)
):

    consulta = (
        db.query(Consulta)
        .filter(Consulta.id == consulta_id)
        .first()
    )

    if not consulta:
        return {
            "erro": "Consulta nao encontrada"
        }

    db.delete(consulta)
    db.commit()

    return {
        "mensagem": "Consulta excluida com sucesso"
    }