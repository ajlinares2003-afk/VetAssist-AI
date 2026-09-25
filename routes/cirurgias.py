from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from database.database import get_db
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
    consulta.status = status_inicial

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
            cirurgia_db.consulta.status = dados.status
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