from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from schemas.animais import AnimalCreate, AnimalResponse
from database.database import get_db
from models.animais import Animal
from models.tutor import Tutor
from services.security import (
    obter_usuario_logado,
    exigir_perfil
)

router = APIRouter(
    prefix="/animais",
    tags=["Animais"]
)

@router.get("/")
def listar_animais(
    usuario_logado: str = Depends(obter_usuario_logado),
    db: Session = Depends(get_db)
):
    animais = (
        db.query(Animal)
        .order_by(Animal.id.desc())
        .all()
    )
    return animais

@router.post("/")
def criar_animal(
    animal: AnimalCreate,
    usuario_logado = Depends(
        exigir_perfil(["ADMIN", "VETERINARIO", "RECEPCAO"])
    ),
    db: Session = Depends(get_db)
):
    tutor = (
        db.query(Tutor)
        .filter(Tutor.id == animal.tutor_id)
        .first()
    )

    if not tutor:
        raise HTTPException(
            status_code=404,
            detail="Tutor não encontrado."
        )

    novo_animal = Animal(
        codigo=animal.codigo,
        nome=animal.nome,
        especie=animal.especie,
        sub_especie=animal.sub_especie,  # <-- Capturando a subespécie corretamente
        raca=animal.raca,
        sexo=animal.sexo,
        idade=animal.idade,
        peso=getattr(animal, "peso", None),
        tutor_id=animal.tutor_id,
        status=animal.status,
        castrado=animal.castrado,
        cor=animal.cor,
        porte=animal.porte
    )

    db.add(novo_animal)
    db.flush()

    if not novo_animal.codigo:
        novo_animal.codigo = f"PET-{novo_animal.id:04d}"

    db.commit()
    db.refresh(novo_animal)

    return novo_animal

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

    if not animal:
        raise HTTPException(
            status_code=404,
            detail="Animal não encontrado."
        )

    return animal

@router.delete("/{animal_id}")
def excluir_animal(
    animal_id: int,
    usuario_logado = Depends(
        exigir_perfil(["ADMIN", "VETERINARIO", "RECEPCAO"])
    ),
    db: Session = Depends(get_db)
):
    animal = (
        db.query(Animal)
        .filter(Animal.id == animal_id)
        .first()
    )

    if not animal:
        raise HTTPException(
            status_code=404,
            detail="Animal não encontrado."
        )

    try:
        db.delete(animal)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não é possível excluir o animal pois existem consultas, vacinas ou prescrições associadas a ele. Altere o status para INATIVO."
        )

    return {"mensagem": "Animal excluído com sucesso."}

@router.put("/{animal_id}")
def atualizar_animal(
    animal_id: int,
    animal: AnimalCreate,
    usuario_logado = Depends(
        exigir_perfil(["ADMIN", "VETERINARIO", "RECEPCAO"])
    ),
    db: Session = Depends(get_db)
):
    animal_db = (
        db.query(Animal)
        .filter(Animal.id == animal_id)
        .first()
    )

    if not animal_db:
        raise HTTPException(
            status_code=404,
            detail="Animal não encontrado."
        )

    tutor = (
        db.query(Tutor)
        .filter(Tutor.id == animal.tutor_id)
        .first()
    )
    
    if not tutor:
        raise HTTPException(
            status_code=404,
            detail="Tutor não encontrado."
        )

    if animal.codigo and animal.codigo != animal_db.codigo:
        codigo_existente = (
            db.query(Animal)
            .filter(Animal.codigo == animal.codigo, Animal.id != animal_id)
            .first()
        )
        if codigo_existente:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"O código '{animal.codigo}' já está em uso por outro animal ({codigo_existente.nome})."
            )

    try:
        if animal.codigo:
            animal_db.codigo = animal.codigo
            
        animal_db.nome = animal.nome
        animal_db.especie = animal.especie
        animal_db.sub_especie = animal.sub_especie  # <-- Atualizando a subespécie corretamente
        animal_db.raca = animal.raca
        animal_db.sexo = animal.sexo
        animal_db.idade = animal.idade
        animal_db.peso = getattr(animal, "peso", None)
        animal_db.tutor_id = animal.tutor_id
        animal_db.status = animal.status
        animal_db.castrado = animal.castrado
        animal_db.cor = animal.cor
        animal_db.porte = animal.porte

        db.commit()
        db.refresh(animal_db)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"O código '{animal.codigo}' já cadastrado no sistema."
        )

    return animal_db