from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from database.database import get_db
from models.triagem import Triagem
from models.consulta import Consulta, StatusAtendimentoEnum
from schemas.triagem import TriagemCreate, TriagemResponse
from services.security import obter_usuario_logado

router = APIRouter(
    prefix="/triagem",
    tags=["Triagem & Pré-Atendimento"]
)


@router.post("/", response_model=TriagemResponse)
def registrar_triagem(
    triagem_data: TriagemCreate,
    db: Session = Depends(get_db),
    usuario_logado: str = Depends(obter_usuario_logado)
):
    # 1. Verifica se a consulta existe
    consulta = db.query(Consulta).filter(Consulta.id == triagem_data.consulta_id).first()
    if not consulta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Atendimento/Consulta não encontrado."
        )

    # 2. Verifica se a consulta já possui triagem cadastrada
    triagem_existente = db.query(Triagem).filter(Triagem.consulta_id == triagem_data.consulta_id).first()
    if triagem_existente:
        # Se já existir, atualiza os dados
        for key, value in triagem_data.model_dump(exclude_unset=True).items():
            setattr(triagem_existente, key, value)
        nova_triagem = triagem_existente
    else:
        # Se não existir, cria um novo registro
        nova_triagem = Triagem(**triagem_data.model_dump())
        db.add(nova_triagem)

    # 3. Avança o status do atendimento para AGUARDANDO_CONSULTA na fila do veterinário
    consulta.status = StatusAtendimentoEnum.AGUARDANDO_CONSULTA
    
    # Atualiza o peso da consulta se informado na triagem
    if triagem_data.peso:
        consulta.peso = triagem_data.peso

    try:
        db.commit()
        db.refresh(nova_triagem)
        db.refresh(consulta)
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao registrar triagem: {str(e)}"
        )

    return nova_triagem


@router.get("/consulta/{consulta_id}", response_model=TriagemResponse)
def buscar_triagem_por_consulta(
    consulta_id: int,
    db: Session = Depends(get_db),
    usuario_logado: str = Depends(obter_usuario_logado)
):
    triagem = db.query(Triagem).filter(Triagem.consulta_id == consulta_id).first()
    if not triagem:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Triagem não encontrada para esta consulta."
        )
    return triagem