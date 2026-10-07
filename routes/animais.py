import time
import requests
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from schemas.animais import AnimalCreate, AnimalResponse
from database.database import get_db, SessionLocal
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

def background_buscar_nome_cientifico(animal_id: int, especie: str, sub_especie: str, raca: str):
    """Busca o nome científico na Groq aguardando a consolidação do registro na base de dados."""
    time.sleep(1)  # Pausa essencial para garantir visibilidade do novo registro em cadastros
    db = SessionLocal()
    try:
        # Busca as configurações no formato chave/valor da tabela configuracoes_sistema
        resultados = db.execute(
            text("SELECT chave, valor FROM configuracoes_sistema")
        ).fetchall()

        config = {row[0]: row[1] for row in resultados}

        # Recolhe a chave e o modelo das configurações oficiais
        api_key = config.get("groq_api_key_1") or config.get("groq_api_key_2")
        modelo = config.get("groq_model_1") or config.get("groq_model_2")

        if not api_key or not modelo:
            print("Aviso Background: Chave ou modelo Groq não encontrados nas configurações do sistema.")
            return

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # Prompt otimizado para evitar falhas em raças exóticas ou específicas
        prompt = (
            f"Retorne apenas o nome científico binomial (gênero e espécie) em formato de texto simples, "
            f"sem pontuações extras ou explicações, para o animal: "
            f"Espécie: {especie}, Sub-espécie/Tipo: {sub_especie}, Raça: {raca}. "
            f"Se desconhecido, retorne o nome científico da espécie principal."
        )

        payload = {
            "model": modelo,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1
        }

        response = requests.post(url, json=payload, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            nome_cientifico = data["choices"][0]["message"]["content"].strip().replace('"', '')
            
            db.execute(
                text("UPDATE animal SET nome_cientifico = :nc WHERE id = :id"),
                {"nc": nome_cientifico, "id": animal_id}
            )
            db.commit()
            print(f"Sucesso: Nome científico '{nome_cientifico}' gravado para o animal ID {animal_id}.")
        else:
            print(f"Erro Groq API ({response.status_code}): {response.text}")
            
    except Exception as e:
        print(f"Exceção crítica em background ao buscar nome científico: {e}")
    finally:
        db.close()

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
    background_tasks: BackgroundTasks,
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
        sub_especie=animal.sub_especie,
        raca=animal.raca,
        nome_cientifico=None,
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

    background_tasks.add_task(
        background_buscar_nome_cientifico,
        novo_animal.id,
        novo_animal.especie,
        novo_animal.sub_especie,
        novo_animal.raca
    )

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
    background_tasks: BackgroundTasks,
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
        animal_db.sub_especie = animal.sub_especie
        animal_db.raca = animal.raca

        if animal_db.especie != animal.especie or animal_db.sub_especie != animal.sub_especie or animal_db.raca != animal.raca or not animal_db.nome_cientifico:
            background_tasks.add_task(
                background_buscar_nome_cientifico,
                animal_db.id,
                animal.especie,
                animal.sub_especie,
                animal.raca
            )

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