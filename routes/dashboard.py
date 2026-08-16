from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session
from database.database import get_db
from models.tutor import Tutor
from models.animais import Animal
from models.consulta import Consulta
from models.exame import Exame
from models.vacina import Vacina
from models.prescricao import Prescricao
from datetime import date

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"]
)

@router.get("/")
def dashboard(
    db: Session = Depends(get_db)
):
    total_tutores = db.query(Tutor).count()
    total_animais = db.query(Animal).count()
    total_consultas = db.query(Consulta).count()
    total_exames = db.query(Exame).count()
    total_vacinas = db.query(Vacina).count()
    total_prescricoes = db.query(Prescricao).count()

    return {
        "total_tutores": total_tutores,
        "total_animais": total_animais,
        "total_consultas": total_consultas,
        "total_exames": total_exames,
        "total_vacinas": total_vacinas,
        "total_prescricoes": total_prescricoes,
        "media_consultas_por_animal":
            round(
                total_consultas / total_animais,
                2
            ) if total_animais > 0 else 0,
        "media_exames_por_consulta":
            round(
                total_exames / total_consultas,
                2
            ) if total_consultas > 0 else 0
    }

@router.get("/especies")
def dashboard_especies(
    db: Session = Depends(get_db)
):
    return {
        "cachorros":
            db.query(Animal)
            .filter(Animal.especie == "Cachorro")
            .count(),
        "gatos":
            db.query(Animal)
            .filter(Animal.especie == "Gato")
            .count()
    }

@router.get("/vacinas")
def dashboard_vacinas(
    db: Session = Depends(get_db)
):
    return {
        "total_vacinas":
            db.query(Vacina).count(),
        "reforcos_futuros":
            db.query(Vacina)
            .filter(Vacina.data_reforco >= date.today())
            .count()
    }