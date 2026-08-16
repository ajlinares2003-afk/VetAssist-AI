from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session
from database.database import get_db
from models.animais import Animal
from models.tutor import Tutor
from models.consulta import Consulta
from models.exame import Exame
from models.vacina import Vacina
from models.prescricao import Prescricao

router = APIRouter(
    prefix="/prontuarios",
    tags=["Prontuários"]
)

@router.get("/{animal_id}")
def obter_prontuario(
    animal_id: int,
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
    tutor = (
        db.query(Tutor)
        .filter(Tutor.id == animal.tutor_id)
        .first()
    )
    vacinas = (
        db.query(Vacina)
        .filter(Vacina.animal_id == animal_id)
        .all()
    )
    consultas = (
        db.query(Consulta)
        .filter(Consulta.animal_id == animal_id)
        .all()
    )

    historico_consultas = []
    for consulta in consultas:
        exames = (
            db.query(Exame)
            .filter(
                Exame.consulta_id == consulta.id
            )
            .all()
        )
        prescricoes = (
            db.query(Prescricao)
            .filter(
                Prescricao.consulta_id == consulta.id
            )
            .all()
        )

        historico_consultas.append(
            {
                "consulta": consulta,
                "exames": exames,
                "prescricoes": prescricoes
            }
        )

    return {
        "animal": animal,
        "tutor": tutor,
        "vacinas": vacinas,
        "consultas": historico_consultas
    }

@router.get("/{animal_id}/resumo")
def obter_resumo(
    animal_id: int,
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
    tutor = (
        db.query(Tutor)
        .filter(Tutor.id == animal.tutor_id)
        .first()
    )
    vacinas = (
        db.query(Vacina)
        .filter(Vacina.animal_id == animal_id)
        .all()
    )
    consultas = (
        db.query(Consulta)
        .filter(Consulta.animal_id == animal_id)
        .all()
    )
    resumo = (
        f"Paciente {animal.nome}, "
        f"{animal.especie.lower()} da raça {animal.raca}, "
        f"{animal.idade} anos, "
        f"{animal.peso} kg. "
    )

    if vacinas:
        nomes_vacinas = []
        for vacina in vacinas:
            nomes_vacinas.append(
                vacina.nome_vacina
            )

        resumo += (
            "Vacinas registradas: "
            + ", ".join(nomes_vacinas)
            + ". "
        )

    for consulta in consultas:
        resumo += (
            f"Em {consulta.data_consulta.strftime('%d/%m/%Y')}, "
            f"apresentou "
            f"{consulta.queixa_principal}. "
        )
        exames = (
            db.query(Exame)
            .filter(
                Exame.consulta_id == consulta.id
            )
            .all()
        )
        for exame in exames:
            resumo += (
                f"Foi realizado "
                f"{exame.nome_exame}, "
                f"com resultado "
                f"{exame.resultado}. "
            )
        prescricoes = (
            db.query(Prescricao)
            .filter(
                Prescricao.consulta_id == consulta.id
            )
            .all()
        )
        for prescricao in prescricoes:
            resumo += (
                f"Prescrito "
                f"{prescricao.medicamento}"
            )
            if prescricao.dosagem:
                resumo += (
                    f" {prescricao.dosagem}"
                )
            if prescricao.frequencia:
                resumo += (
                    f" a cada "
                    f"{prescricao.frequencia}"
                )
            if prescricao.duracao:
                resumo += (
                    f" por "
                    f"{prescricao.duracao}"
                )
            resumo += ". "

    return {
        "animal_id": animal.id,
        "animal": animal.nome,
        "resumo": resumo
    }