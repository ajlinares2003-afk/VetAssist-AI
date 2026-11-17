import re
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException       
from sqlalchemy import text
from sqlalchemy.orm import Session
from database import get_db
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
    usuario_logado: str = Depends(
        obter_usuario_logado
    ),
    db: Session = Depends(get_db)
):
    vacinas = db.query(Vacina).all()
    return vacinas

@router.post("/")
def criar_vacina(
    vacina: VacinaCreate,
    usuario_logado = Depends(
        exigir_perfil(
            ["ADMIN", "VETERINARIO", "RECEPCAO"]
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
        raise HTTPException(
            status_code=404,
            detail="Animal nao encontrado"
        )   

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

def _data_iso(d):
    return d.isoformat() if d else None


@router.get("/completas")
def listar_vacinas_completas(
    usuario_logado: str = Depends(obter_usuario_logado),
    db: Session = Depends(get_db)
):
    """Vacinas ja com paciente e tutor (tela de controle, carteira e lembrete de reforco)."""
    linhas = db.execute(text("""
        SELECT v.id, v.animal_id, v.nome_vacina, v.fabricante, v.dose, v.lote,
               v.data_aplicacao, v.data_reforco, v.observacoes,
               a.nome AS animal_nome, a.codigo AS animal_codigo, a.especie, a.raca,
               a.sexo, a.idade, a.peso, a.data_nascimento,
               t.nome AS tutor_nome, t.telefone AS tutor_telefone
        FROM vacina v
        LEFT JOIN animal a ON a.id = v.animal_id
        LEFT JOIN tutor t ON t.id = a.tutor_id
        ORDER BY v.data_aplicacao DESC NULLS LAST, v.id DESC
    """)).mappings().all()
    saida = []
    for l in linhas:
        d = dict(l)
        d["data_aplicacao"] = _data_iso(d["data_aplicacao"])
        d["data_reforco"] = _data_iso(d["data_reforco"])
        d["data_nascimento"] = _data_iso(d["data_nascimento"])
        d["idade"] = float(d["idade"]) if d["idade"] is not None else None
        d["peso"] = float(d["peso"]) if d["peso"] is not None else None
        saida.append(d)
    return saida


@router.get("/pendentes")
def listar_vacinas_pendentes(
    dias: int = 60,
    usuario_logado: str = Depends(obter_usuario_logado),
    db: Session = Depends(get_db)
):
    """Atendimentos de vacinacao (vindos da Agenda) que ainda nao tem a aplicacao registrada."""
    linhas = db.execute(text("""
        SELECT c.id AS consulta_id, c.codigo AS consulta_codigo, c.data_consulta, c.status,
               c.queixa_principal,
               a.id AS animal_id, a.nome AS animal_nome, a.codigo AS animal_codigo,
               a.especie, a.idade, a.data_nascimento,
               t.nome AS tutor_nome
        FROM consulta c
        JOIN animal a ON a.id = c.animal_id
        LEFT JOIN tutor t ON t.id = a.tutor_id
        WHERE c.queixa_principal ILIKE 'Agendado: Vacina%'
          AND c.data_consulta >= (now() - make_interval(days => :dias))
          AND COALESCE(c.status, '') NOT ILIKE 'cancel%'
          AND NOT EXISTS (
                SELECT 1 FROM vacina v
                WHERE v.animal_id = a.id
                  AND v.data_aplicacao = (c.data_consulta AT TIME ZONE 'America/Sao_Paulo')::date
          )
        ORDER BY c.data_consulta DESC
    """), {"dias": max(1, int(dias or 60))}).mappings().all()
    saida = []
    for l in linhas:
        d = dict(l)
        m = re.match(r"\s*Agendado:\s*Vacina\w*\s*-\s*(.+)", d.pop("queixa_principal") or "", re.IGNORECASE)
        nome = (m.group(1).strip() if m else "")
        nome = re.sub(r"^(vacina[cç][aã]o|vacina)\s+", "", nome, flags=re.IGNORECASE).strip()
        d["vacina_sugerida"] = nome
        d["data_consulta"] = d["data_consulta"].isoformat() if d["data_consulta"] else None
        d["data_nascimento"] = _data_iso(d["data_nascimento"])
        d["idade"] = float(d["idade"]) if d["idade"] is not None else None
        saida.append(d)
    return saida


@router.get("/{vacina_id}")
def buscar_vacina(
    vacina_id: int,
    db: Session = Depends(get_db)
):
    vacina = (
        db.query(Vacina)
        .filter(
            Vacina.id == vacina_id
        )
        .first()
    )

    if not vacina:
        raise HTTPException(
            status_code=404,
            detail="Vacina nao encontrada"
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
        raise HTTPException(
            status_code=404,
            detail="Vacina nao encontrada"
        )

    animal = (
        db.query(Animal)
        .filter(Animal.id == vacina.animal_id)
        .first()
    )

    if not animal:
        raise HTTPException(
            status_code=404,
            detail="Animal nao encontrado"
        )
    
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
        raise HTTPException(
            status_code=404,
            detail="Vacina nao encontrada"
        )

    db.delete(vacina)
    db.commit()

    return {
        "mensagem": "Vacina excluida com sucesso"
    }