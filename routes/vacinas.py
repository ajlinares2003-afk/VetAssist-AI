from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session
from database.database import get_db
from models.vacina import Vacina
from models.animais import Animal
from schemas.vacina import VacinaCreate
from services.security import (
    obter_usuario_logado,
    exigir_perfil
)

router = APIRouter(
    prefix="/vacinas",
    tags=["Vacinas"]
)

@router.get("/")
def listar_vacinas(
    db: Session = Depends(get_db)
):
    vacinas = db.query(Vacina).all()
    return vacinas

@router.post("/")
def criar_vacina(
    vacina: VacinaCreate,
    usuario_logado = Depends(
        exigir_perfil(
            ["ADMIN", "VETERINARIO"]
        )
    ),
    db: Session = Depends(get_db)
):
    animal = (
        db.query(Animal)
        .filter(Animal.id == vacina.animal_id)
        .first()
    )

    if not animal:
        return {
            "erro": "Animal nao encontrado"
        }

    nova_vacina = Vacina(
        animal_id=vacina.animal_id,
        nome_vacina=vacina.nome_vacina,
        fabricante=vacina.fabricante,
        lote=vacina.lote,
        dose=vacina.dose,
        data_aplicacao=vacina.data_aplicacao,
        data_reforco=vacina.data_reforco,
        observacoes=vacina.observacoes
    )

    db.add(nova_vacina)
    db.commit()
    db.refresh(nova_vacina)
    return nova_vacina

@router.get("/{vacina_id}")
def buscar_vacina(
    vacina_id: int,
    db: Session = Depends(get_db)
):
    vacina = (
        db.query(Vacina)
        .filter(Vacina.id == vacina_id)
        .first()
    )
    return vacina

@router.put("/{vacina_id}")
def atualizar_vacina(
    vacina_id: int,
    vacina: VacinaCreate,
    usuario_logado = Depends(
        exigir_perfil(
            ["ADMIN", "VETERINARIO"]
        )
    ),
    db: Session = Depends(get_db)
):
    vacina_db = (
        db.query(Vacina)
        .filter(Vacina.id == vacina_id)
        .first()
    )
    if not vacina_db:
        return {
            "erro": "Vacina nao encontrada"
        }
    vacina_db.animal_id = vacina.animal_id
    vacina_db.nome_vacina = vacina.nome_vacina
    vacina_db.fabricante = vacina.fabricante
    vacina_db.lote = vacina.lote
    vacina_db.dose = vacina.dose
    vacina_db.data_aplicacao = vacina.data_aplicacao
    vacina_db.data_reforco = vacina.data_reforco
    vacina_db.observacoes = vacina.observacoes

    db.commit()
    db.refresh(vacina_db)
    return vacina_db

@router.delete("/{vacina_id}")
def excluir_vacina(
    vacina_id: int,
    usuario_logado = Depends(
        exigir_perfil(
            ["ADMIN", "VETERINARIO"]
        )
    ),
    db: Session = Depends(get_db)
):
    vacina = (
        db.query(Vacina)
        .filter(Vacina.id == vacina_id)
        .first()
    )
    if not vacina:
        return {
            "erro": "Vacina nao encontrada"
        }
    db.delete(vacina)
    db.commit()

    return {
        "mensagem": "Vacina excluida com sucesso"
    }