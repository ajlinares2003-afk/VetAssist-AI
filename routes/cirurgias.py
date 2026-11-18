from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import List, Optional

from database import get_db
from models.cirurgia import ProcedimentoCirurgico, FichaAnestesica, MonitoramentoAnestesico
from models.consulta import Consulta
from models.usuario import Usuario
from schemas.cirurgia import (
    CirurgiaCreate,
    CirurgiaUpdate,
    CirurgiaResponse,
    FichaAnestesicaCreate,
    FichaAnestesicaResponse,
    MonitoramentoCreate,
    MonitoramentoResponse
)
from services.security import obter_usuario_logado, exigir_perfil

router = APIRouter(
    prefix="/cirurgias",
    tags=["Centro Cirúrgico & Anestesiologia"]
)

def _status_consulta(db: Session, status_cirurgia: str, consulta_id: int) -> str:
    """Status do atendimento a partir do status da cirurgia.
    Ao concluir, o paciente volta para a internacao (se estiver internado) ou segue para o pagamento."""
    if status_cirurgia == "Concluída":
        internado = db.execute(text(
            "SELECT 1 FROM internacao WHERE consulta_id = :c AND status = 'INTERNADO' LIMIT 1"
        ), {"c": consulta_id}).first()
        return "Em Internação UTI" if internado else "Aguardando Pagamento"
    return status_cirurgia


# --- ROTAS DE PROCEDIMENTOS CIRÚRGICOS ---

@router.get("/", response_model=List[CirurgiaResponse])
def listar_cirurgias(
    db: Session = Depends(get_db),
    usuario_logado: str = Depends(obter_usuario_logado)
):
    cirurgias = db.query(ProcedimentoCirurgico).order_by(ProcedimentoCirurgico.id.desc()).all()
    return cirurgias


@router.post("/", response_model=CirurgiaResponse)
def criar_cirurgia(
    cirurgia_data: CirurgiaCreate,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    # 1. Valida se a consulta de origem existe
    consulta = db.query(Consulta).filter(Consulta.id == cirurgia_data.consulta_id).first()
    if not consulta:
        raise HTTPException(status_code=404, detail="Consulta vinculada não encontrada.")

    # 2. Sincroniza automaticamente o status da consulta de origem
    status_inicial = cirurgia_data.status or "Aguardando Cirurgia"
    consulta.status = _status_consulta(db, status_inicial, consulta.id)

    # 3. Cria o procedimento cirúrgico com o status inicial enviado ou padrão
    nova_cirurgia = ProcedimentoCirurgico(
        consulta_id=cirurgia_data.consulta_id,
        descricao_tecnica=cirurgia_data.descricao_tecnica,
        classificacao_asa=cirurgia_data.classificacao_asa,
        status=status_inicial,
        data_hora_inicio=cirurgia_data.data_hora_inicio,
        data_hora_fim=cirurgia_data.data_hora_fim,
        observacoes_pos_operatorias=cirurgia_data.observacoes_pos_operatorias
    )

    # 4. Associa a equipe cirúrgica informada
    if cirurgia_data.equipe_cirurgica_ids:
        membros = db.query(Usuario).filter(Usuario.id.in_(cirurgia_data.equipe_cirurgica_ids)).all()
        nova_cirurgia.equipe = membros

    db.add(nova_cirurgia)
    db.flush()

    # 5. Gera o código sequencial automático CIR-001, CIR-002...
    if not nova_cirurgia.codigo:
        nova_cirurgia.codigo = f"CIR-{nova_cirurgia.id:03d}"

    db.commit()
    db.refresh(nova_cirurgia)
    return nova_cirurgia


# --- BIBLIOTECA DE FARMACOS ANESTESICOS (calculo de dose por peso) ---

CAMPOS_FARMACO = (
    "id, nome, classe, especie, dose_min, dose_max, dose_padrao, unidade_dose, "
    "concentracao, unidade_conc, via, observacoes, status, validado_por, validado_em, ativo"
)


class FarmacoIn(BaseModel):
    nome: str
    classe: str
    especie: str
    dose_min: float
    dose_max: float
    dose_padrao: Optional[float] = None
    unidade_dose: str = "mg/kg"
    concentracao: Optional[float] = None
    unidade_conc: Optional[str] = None
    via: Optional[str] = None
    observacoes: Optional[str] = None
    ativo: bool = True
    validado: bool = False


def _farmaco_dict(l):
    d = dict(l)
    for k in ("dose_min", "dose_max", "dose_padrao", "concentracao"):
        d[k] = float(d[k]) if d.get(k) is not None else None
    d["validado_em"] = d["validado_em"].isoformat() if d.get("validado_em") else None
    return d


def _nome_usuario(u) -> str:
    return getattr(u, "nome", None) or getattr(u, "email", None) or (u if isinstance(u, str) else "Veterinário")


def _validar_farmaco(f: FarmacoIn):
    if f.dose_min <= 0 or f.dose_max < f.dose_min:
        raise HTTPException(status_code=400, detail="Doses inválidas: a dose mínima deve ser maior que zero e menor ou igual à máxima.")
    if f.dose_padrao is not None and not (f.dose_min <= f.dose_padrao <= f.dose_max):
        raise HTTPException(status_code=400, detail="A dose padrão deve estar entre a mínima e a máxima.")
    if f.especie not in ("CANINO", "FELINO"):
        raise HTTPException(status_code=400, detail="Espécie deve ser CANINO ou FELINO.")
    if f.unidade_dose not in ("mg/kg", "mcg/kg", "mL/kg", "mL/kg/h"):
        raise HTTPException(status_code=400, detail="Unidade de dose inválida.")
    if f.concentracao is not None and (f.concentracao <= 0 or f.unidade_conc not in ("mg/mL", "mcg/mL")):
        raise HTTPException(status_code=400, detail="Informe a concentração (maior que zero) e a unidade (mg/mL ou mcg/mL).")


@router.get("/farmacos")
def listar_farmacos(
    incluir_inativos: bool = False,
    db: Session = Depends(get_db),
    usuario_logado: str = Depends(obter_usuario_logado)
):
    filtro = "" if incluir_inativos else "WHERE ativo = true"
    linhas = db.execute(text(f"SELECT {CAMPOS_FARMACO} FROM anestesia_farmaco {filtro} ORDER BY classe, nome, especie")).mappings().all()
    return [_farmaco_dict(l) for l in linhas]


@router.post("/farmacos")
def criar_farmaco(
    dados: FarmacoIn,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    _validar_farmaco(dados)
    r = db.execute(text("""
        INSERT INTO anestesia_farmaco
          (nome, classe, especie, dose_min, dose_max, dose_padrao, unidade_dose, concentracao, unidade_conc,
           via, observacoes, status, validado_por, validado_em, ativo)
        VALUES
          (:nome, :classe, :especie, :dmin, :dmax, :dpad, :un, :conc, :uc, :via, :obs,
           :status, :vpor, CASE WHEN CAST(:val AS boolean) THEN now() ELSE NULL END, :ativo)
        RETURNING id
    """), {
        "nome": dados.nome.strip(), "classe": dados.classe, "especie": dados.especie,
        "dmin": dados.dose_min, "dmax": dados.dose_max, "dpad": dados.dose_padrao, "un": dados.unidade_dose,
        "conc": dados.concentracao, "uc": dados.unidade_conc if dados.concentracao else None,
        "via": dados.via, "obs": dados.observacoes,
        "status": "VALIDADO" if dados.validado else "RASCUNHO",
        "vpor": _nome_usuario(usuario_logado) if dados.validado else None,
        "val": dados.validado, "ativo": dados.ativo,
    }).first()
    db.commit()
    return {"id": r[0]}


@router.put("/farmacos/{farmaco_id}")
def atualizar_farmaco(
    farmaco_id: int,
    dados: FarmacoIn,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    """Qualquer alteracao de dose/concentracao volta o farmaco para RASCUNHO, a menos que o
    veterinario o marque como validado na mesma gravacao (fica registrado quem validou e quando)."""
    _validar_farmaco(dados)
    existe = db.execute(text("SELECT 1 FROM anestesia_farmaco WHERE id = :i"), {"i": farmaco_id}).first()
    if not existe:
        raise HTTPException(status_code=404, detail="Fármaco não encontrado.")
    db.execute(text("""
        UPDATE anestesia_farmaco SET
          nome = :nome, classe = :classe, especie = :especie, dose_min = :dmin, dose_max = :dmax,
          dose_padrao = :dpad, unidade_dose = :un, concentracao = :conc, unidade_conc = :uc,
          via = :via, observacoes = :obs, ativo = :ativo,
          status = :status,
          validado_por = :vpor,
          validado_em = CASE WHEN CAST(:val AS boolean) THEN now() ELSE NULL END
        WHERE id = :i
    """), {
        "i": farmaco_id, "nome": dados.nome.strip(), "classe": dados.classe, "especie": dados.especie,
        "dmin": dados.dose_min, "dmax": dados.dose_max, "dpad": dados.dose_padrao, "un": dados.unidade_dose,
        "conc": dados.concentracao, "uc": dados.unidade_conc if dados.concentracao else None,
        "via": dados.via, "obs": dados.observacoes, "ativo": dados.ativo,
        "status": "VALIDADO" if dados.validado else "RASCUNHO",
        "vpor": _nome_usuario(usuario_logado) if dados.validado else None,
        "val": dados.validado,
    })
    db.commit()
    return {"mensagem": "Fármaco atualizado."}


@router.get("/{cirurgia_id}/paciente")
def paciente_da_cirurgia(
    cirurgia_id: int,
    db: Session = Depends(get_db),
    usuario_logado: str = Depends(obter_usuario_logado)
):
    """Paciente do procedimento, com o peso do ATENDIMENTO (usado no calculo de doses)."""
    l = db.execute(text("""
        SELECT a.id AS animal_id, a.nome, a.codigo, a.especie, a.raca, a.sexo, a.idade,
               COALESCE(c.peso_atendimento, a.peso) AS peso,
               (c.peso_atendimento IS NOT NULL) AS peso_do_atendimento,
               t.nome AS tutor_nome, c.codigo AS consulta_codigo
        FROM procedimentos_cirurgicos p
        JOIN consulta c ON c.id = p.consulta_id
        JOIN animal a ON a.id = c.animal_id
        LEFT JOIN tutor t ON t.id = a.tutor_id
        WHERE p.id = :i
    """), {"i": cirurgia_id}).mappings().first()
    if not l:
        raise HTTPException(status_code=404, detail="Procedimento cirúrgico não encontrado.")
    d = dict(l)
    d["peso"] = float(d["peso"]) if d["peso"] is not None else None
    d["idade"] = float(d["idade"]) if d["idade"] is not None else None
    return d


@router.get("/{cirurgia_id}", response_model=CirurgiaResponse)
def buscar_cirurgia(
    cirurgia_id: int,
    db: Session = Depends(get_db),
    usuario_logado: str = Depends(obter_usuario_logado)
):
    cirurgia = db.query(ProcedimentoCirurgico).filter(ProcedimentoCirurgico.id == cirurgia_id).first()
    if not cirurgia:
        raise HTTPException(status_code=404, detail="Procedimento cirúrgico não encontrado.")
    return cirurgia


@router.put("/{cirurgia_id}", response_model=CirurgiaResponse)
def atualizar_cirurgia(
    cirurgia_id: int,
    dados: CirurgiaUpdate,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    cirurgia_db = db.query(ProcedimentoCirurgico).filter(ProcedimentoCirurgico.id == cirurgia_id).first()
    if not cirurgia_db:
        raise HTTPException(status_code=404, detail="Procedimento cirúrgico não encontrado.")

    if dados.descricao_tecnica is not None:
        cirurgia_db.descricao_tecnica = dados.descricao_tecnica
    if dados.classificacao_asa is not None:
        cirurgia_db.classificacao_asa = dados.classificacao_asa
    if dados.status is not None:
        cirurgia_db.status = dados.status
        # Sincroniza também o status da consulta vinculada ao atualizar
        if cirurgia_db.consulta:
            cirurgia_db.consulta.status = _status_consulta(db, dados.status, cirurgia_db.consulta_id)
    if dados.data_hora_inicio is not None:
        cirurgia_db.data_hora_inicio = dados.data_hora_inicio
    if dados.data_hora_fim is not None:
        cirurgia_db.data_hora_fim = dados.data_hora_fim
    if dados.observacoes_pos_operatorias is not None:
        cirurgia_db.observacoes_pos_operatorias = dados.observacoes_pos_operatorias

    db.commit()
    db.refresh(cirurgia_db)
    return cirurgia_db


# --- ROTAS DE FICHA ANESTÉSICA ---

@router.post("/fichas-anestesicas", response_model=FichaAnestesicaResponse)
def salvar_ficha_anestesica(
    dados: FichaAnestesicaCreate,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    # Verifica se a cirurgia existe
    cirurgia = db.query(ProcedimentoCirurgico).filter(ProcedimentoCirurgico.id == dados.cirurgia_id).first()
    if not cirurgia:
        raise HTTPException(status_code=404, detail="Procedimento cirúrgico não encontrado.")

    # Verifica se já existe ficha para esta cirurgia
    ficha_existente = db.query(FichaAnestesica).filter(FichaAnestesica.procedimento_id == dados.cirurgia_id).first()
    
    if ficha_existente:
        ficha_existente.protocolo_mpa = dados.protocolo_mpa
        ficha_existente.protocolo_inducao = dados.protocolo_inducao
        ficha_existente.protocolo_manutencao = dados.protocolo_manutencao
        ficha_existente.fluidoterapia = dados.fluidoterapia
        db.commit()
        db.refresh(ficha_existente)
        return ficha_existente

    nova_ficha = FichaAnestesica(
        procedimento_id=dados.cirurgia_id,
        protocolo_mpa=dados.protocolo_mpa,
        protocolo_inducao=dados.protocolo_inducao,
        protocolo_manutencao=dados.protocolo_manutencao,
        fluidoterapia=dados.fluidoterapia
    )
    db.add(nova_ficha)
    db.commit()
    db.refresh(nova_ficha)
    return nova_ficha


@router.get("/fichas-anestesicas/{cirurgia_id}", response_model=FichaAnestesicaResponse)
def buscar_ficha_anestesica(
    cirurgia_id: int,
    db: Session = Depends(get_db),
    usuario_logado: str = Depends(obter_usuario_logado)
):
    ficha = db.query(FichaAnestesica).filter(FichaAnestesica.procedimento_id == cirurgia_id).first()
    if not ficha:
        raise HTTPException(status_code=404, detail="Ficha anestésica não encontrada para este procedimento.")
    return ficha


# --- ROTAS DE MONITORAMENTO TRANSOPERATÓRIO (AFERIÇÕES) ---

@router.post("/monitoramento", response_model=MonitoramentoResponse)
def registrar_monitoramento(
    dados: MonitoramentoCreate,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    ficha = db.query(FichaAnestesica).filter(FichaAnestesica.id == dados.ficha_anestesica_id).first()
    if not ficha:
        raise HTTPException(status_code=404, detail="Ficha anestésica de referência não encontrada.")

    novo_monitoramento = MonitoramentoAnestesico(
        ficha_anestesica_id=dados.ficha_anestesica_id,
        tempo_minutos=dados.tempo_minutos,
        frequencia_cardiaca=dados.frequencia_cardiaca,
        frequencia_respiratoria=dados.frequencia_respiratoria,
        temperatura=dados.temperatura,
        spo2=dados.spo2,
        pas=dados.pas,
        pad=dados.pad
    )
    db.add(novo_monitoramento)
    db.commit()
    db.refresh(novo_monitoramento)
    return novo_monitoramento


@router.get("/monitoramento/{ficha_id}", response_model=List[MonitoramentoResponse])
def listar_monitoramentos_ficha(
    ficha_id: int,
    db: Session = Depends(get_db),
    usuario_logado: str = Depends(obter_usuario_logado)
):
    monitoramentos = (
        db.query(MonitoramentoAnestesico)
        .filter(MonitoramentoAnestesico.ficha_anestesica_id == ficha_id)
        .order_by(MonitoramentoAnestesico.tempo_minutos.asc())
        .all()
    )
    return monitoramentos


# --- ROTA AUXILIAR: LISTAR APENAS VETERINÁRIOS PARA O CENTRO CIRÚRGICO ---

@router.get("/usuarios/veterinarios")
def listar_veterinarios_cirurgia(
    db: Session = Depends(get_db),
    usuario_logado: str = Depends(obter_usuario_logado)
):
    veterinarios = db.query(Usuario).filter(Usuario.perfil == "VETERINARIO").all()
    return veterinarios