from typing import Optional, List
from datetime import datetime, date, timedelta
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import get_db
from models.agendamento import Agendamento
from models.consulta import Consulta
from models.animais import Animal
from models.usuario import Usuario
from services.security import obter_usuario_logado, exigir_perfil

router = APIRouter(prefix="/agendamentos", tags=["Agenda de Atendimentos"])

PERFIS = ["ADMIN", "VETERINARIO", "RECEPCAO"]
TIPOS_SERVICO = ["Consulta", "Vacina", "Retorno", "Exame", "Cirurgia"]
STATUS_VALIDOS = ["AGENDADO", "EM_ESPERA", "CANCELADO", "FALTOU"]
# De onde cada status pode ir (EM_ESPERA = paciente na recepção, a consulta já foi criada)
TRANSICOES = {
    "AGENDADO": ["EM_ESPERA", "CANCELADO", "FALTOU"],
    "EM_ESPERA": [],
    "FALTOU": ["AGENDADO"],
    "CANCELADO": [],
}


class AgendamentoCreate(BaseModel):
    animal_id: int
    usuario_id: Optional[int] = None
    data_horario: datetime
    tipo_servico: Optional[str] = "Consulta"
    observacoes: Optional[str] = None


class AgendamentoEdit(BaseModel):
    usuario_id: Optional[int] = None
    data_horario: Optional[datetime] = None
    tipo_servico: Optional[str] = None
    observacoes: Optional[str] = None


def _sem_tz(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _verificar_conflitos(db: Session, animal_id: int, usuario_id: Optional[int], horario: datetime, ignorar_id: Optional[int] = None):
    """Mesmo veterinário (ou mesmo paciente) não pode ter dois agendamentos ativos no mesmo horário."""
    ativos = db.query(Agendamento).filter(
        Agendamento.data_horario == horario,
        Agendamento.status.in_(["AGENDADO", "EM_ESPERA"]),
    )
    if ignorar_id:
        ativos = ativos.filter(Agendamento.id != ignorar_id)
    for outro in ativos.all():
        if outro.animal_id == animal_id:
            raise HTTPException(status_code=409, detail="Este paciente já tem um agendamento neste horário.")
        if usuario_id and outro.usuario_id == usuario_id:
            raise HTTPException(status_code=409, detail="O veterinário escolhido já tem um agendamento neste horário.")


def _serializar(a, animais, usuarios):
    animal = animais.get(a.animal_id)
    vet = usuarios.get(a.usuario_id) if a.usuario_id else None
    nome_tutor = "-"
    if animal:
        if getattr(animal, "tutor", None):
            nome_tutor = animal.tutor.nome
        elif getattr(animal, "tutor_nome", None):
            nome_tutor = animal.tutor_nome
    return {
        "id": a.id,
        "animal_id": a.animal_id,
        "pet_nome": animal.nome if animal else "Paciente",
        "especie": animal.especie if animal else "-",
        "tutor_nome": nome_tutor,
        "veterinario_id": a.usuario_id,
        "veterinario_nome": vet.nome if vet else "Qualquer Veterinário",
        "data_horario": a.data_horario.strftime("%Y-%m-%d %H:%M"),
        "data": a.data_horario.strftime("%Y-%m-%d"),
        "hora": a.data_horario.strftime("%H:%M"),
        "tipo_servico": a.tipo_servico,
        "status": a.status,
        "observacoes": a.observacoes,
    }


@router.get("/")
def listar_agendamentos(
    data_consulta: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    db: Session = Depends(get_db),
    usuario_logado=Depends(obter_usuario_logado),
):
    """Lista por dia (data_consulta) ou por período (data_inicio..data_fim, ambos inclusos)."""
    query = db.query(Agendamento)
    try:
        if data_consulta:
            alvo = datetime.strptime(data_consulta, "%Y-%m-%d").date()
            query = query.filter(func.date(Agendamento.data_horario) == alvo)
        else:
            if data_inicio:
                query = query.filter(func.date(Agendamento.data_horario) >= datetime.strptime(data_inicio, "%Y-%m-%d").date())
            if data_fim:
                query = query.filter(func.date(Agendamento.data_horario) <= datetime.strptime(data_fim, "%Y-%m-%d").date())
    except ValueError:
        raise HTTPException(status_code=422, detail="Data inválida. Use o formato AAAA-MM-DD.")

    agendamentos = query.order_by(Agendamento.data_horario.asc()).all()

    # Uma consulta por tabela (antes eram duas por agendamento)
    animais = {x.id: x for x in db.query(Animal).filter(Animal.id.in_({a.animal_id for a in agendamentos})).all()} if agendamentos else {}
    ids_vet = {a.usuario_id for a in agendamentos if a.usuario_id}
    usuarios = {x.id: x for x in db.query(Usuario).filter(Usuario.id.in_(ids_vet)).all()} if ids_vet else {}
    return [_serializar(a, animais, usuarios) for a in agendamentos]


@router.post("/")
def criar_agendamento(
    dados: AgendamentoCreate,
    db: Session = Depends(get_db),
    usuario_logado=Depends(exigir_perfil(PERFIS)),
):
    if not db.query(Animal).filter(Animal.id == dados.animal_id).first():
        raise HTTPException(status_code=404, detail="Paciente não encontrado.")
    if dados.usuario_id and not db.query(Usuario).filter(Usuario.id == dados.usuario_id).first():
        raise HTTPException(status_code=404, detail="Veterinário não encontrado.")

    tipo = (dados.tipo_servico or "Consulta").strip()
    if tipo not in TIPOS_SERVICO:
        raise HTTPException(status_code=422, detail=f"Tipo de serviço inválido. Use: {', '.join(TIPOS_SERVICO)}.")
    horario = _sem_tz(dados.data_horario)
    _verificar_conflitos(db, dados.animal_id, dados.usuario_id, horario)

    novo = Agendamento(
        animal_id=dados.animal_id,
        usuario_id=dados.usuario_id,
        data_horario=horario,
        tipo_servico=tipo,
        status="AGENDADO",
        observacoes=(dados.observacoes or "").strip()[:500] or None,
    )
    db.add(novo)
    db.commit()
    db.refresh(novo)
    return {"mensagem": "Agendamento criado com sucesso!", "id": novo.id}


@router.put("/{agendamento_id}")
def editar_agendamento(
    agendamento_id: int,
    dados: AgendamentoEdit,
    db: Session = Depends(get_db),
    usuario_logado=Depends(exigir_perfil(PERFIS)),
):
    """Remarcar / trocar veterinário / corrigir observação. Só enquanto está AGENDADO."""
    ag = db.query(Agendamento).filter(Agendamento.id == agendamento_id).first()
    if not ag:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado.")
    if ag.status != "AGENDADO":
        raise HTTPException(status_code=409, detail="Só é possível editar agendamentos com status Agendado.")

    campos = dados.model_dump(exclude_unset=True) if hasattr(dados, "model_dump") else dados.dict(exclude_unset=True)
    horario = _sem_tz(campos["data_horario"]) if campos.get("data_horario") else ag.data_horario
    vet = campos["usuario_id"] if "usuario_id" in campos else ag.usuario_id
    if vet and not db.query(Usuario).filter(Usuario.id == vet).first():
        raise HTTPException(status_code=404, detail="Veterinário não encontrado.")
    tipo = campos.get("tipo_servico") or ag.tipo_servico
    if tipo not in TIPOS_SERVICO:
        raise HTTPException(status_code=422, detail=f"Tipo de serviço inválido. Use: {', '.join(TIPOS_SERVICO)}.")

    _verificar_conflitos(db, ag.animal_id, vet, horario, ignorar_id=ag.id)
    ag.data_horario, ag.usuario_id, ag.tipo_servico = horario, vet, tipo
    if "observacoes" in campos:
        ag.observacoes = (campos["observacoes"] or "").strip()[:500] or None
    db.commit()
    return {"mensagem": "Agendamento atualizado."}


@router.put("/{agendamento_id}/status")
def atualizar_status(
    agendamento_id: int,
    novo_status: str = Query(...),
    db: Session = Depends(get_db),
    usuario_logado=Depends(exigir_perfil(PERFIS)),
):
    if novo_status not in STATUS_VALIDOS:
        raise HTTPException(status_code=422, detail=f"Status inválido. Use: {', '.join(STATUS_VALIDOS)}.")

    ag = db.query(Agendamento).filter(Agendamento.id == agendamento_id).first()
    if not ag:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado.")
    if novo_status == ag.status:
        return {"mensagem": f"Status já é {novo_status}"}
    if novo_status not in TRANSICOES.get(ag.status, []):
        raise HTTPException(status_code=409, detail=f"Não é possível mudar de {ag.status} para {novo_status}.")

    try:
        ag.status = novo_status
        if novo_status == "EM_ESPERA":
            # consulta.usuario_id é obrigatório: "Qualquer veterinário" cai no veterinário/admin padrão
            id_vet = ag.usuario_id
            if not id_vet:
                padrao = db.query(Usuario).filter(Usuario.perfil.in_(["VETERINARIO", "ADMIN"])).order_by(Usuario.id.asc()).first()
                if not padrao:
                    raise HTTPException(status_code=404, detail="Nenhum veterinário cadastrado para receber o atendimento.")
                id_vet = padrao.id
            obs = f" - {ag.observacoes}" if ag.observacoes else ""
            nova = Consulta(
                animal_id=ag.animal_id,
                usuario_id=id_vet,
                queixa_principal=f"Agendado: {ag.tipo_servico}{obs}",
                status="Aguardando Triagem (Recepção)",
                data_consulta=datetime.now(),
            )
            db.add(nova)
            db.flush()
            if hasattr(nova, "codigo") and not nova.codigo:
                nova.codigo = f"CNS-{nova.id:04d}"
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar o agendamento: {e}")
    return {"mensagem": f"Status atualizado para {novo_status}"}