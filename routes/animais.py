from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session
from schemas.animais import AnimalCreate
from database.database import get_db
from models.animais import Animal
from services.security import obter_usuario_logado

router = APIRouter(
    prefix="/animais",
    tags=["Animais"]
)

@router.get("/")
def listar_animais(
    usuario_logado: str = Depends(
        obter_usuario_logado
    ),
    db: Session = Depends(get_db)
):
    animais = db.query(Animal).all()

    return animais

@router.post("/")
def criar_animal(
    animal: AnimalCreate,
    usuario_logado: str = Depends(
        obter_usuario_logado
    ),
    db: Session = Depends(get_db)
):
    novo_animal = Animal(
        nome=animal.nome,
        especie=animal.especie,
        raca=animal.raca,
        sexo=animal.sexo,
        idade=animal.idade,
        peso=animal.peso,
        tutor_id=animal.tutor_id
    )

    db.add(novo_animal)
    db.commit()
    db.refresh(novo_animal)

    return {
        "id": novo_animal.id,
        "nome": novo_animal.nome,
        "tutor_id": novo_animal.tutor_id
    }

@router.get("/{animal_id}")
def buscar_animal(
    animal_id: int,
    db: Session = Depends(get_db)
):
    animal = (
        db.query(Animal)
        .filter(Animal.id == animal_id)
        .first()
    )

    return animal

@router.delete("/{animal_id}")
def excluir_animal(
    animal_id: int,
    usuario_logado: str = Depends(
        obter_usuario_logado
    ),
    db: Session = Depends(get_db)
):
    animal = (
        db.query(Animal)
        .filter(Animal.id == animal_id)
        .first()
    )

    if not animal:
        return {
            "erro": "Animal nao encontrado"
        }

    db.delete(animal)
    db.commit()

    return {
        "mensagem": "Animal excluido com sucesso"
    }

@router.put("/{animal_id}")
def atualizar_animal(
    animal_id: int,
    animal: AnimalCreate,
    usuario_logado: str = Depends(
        obter_usuario_logado
    ),
    db: Session = Depends(get_db)
):
    animal_db = (
        db.query(Animal)
        .filter(Animal.id == animal_id)
        .first()
    )

    if not animal_db:
        return {
            "erro": "Animal nao encontrado"
        }

    animal_db.nome = animal.nome
    animal_db.especie = animal.especie
    animal_db.raca = animal.raca
    animal_db.sexo = animal.sexo
    animal_db.idade = animal.idade
    animal_db.peso = animal.peso
    animal_db.tutor_id = animal.tutor_id

    db.commit()
    db.refresh(animal_db)

    return {
        "id": animal_db.id,
        "nome": animal_db.nome,
        "tutor_id": animal_db.tutor_id
    }