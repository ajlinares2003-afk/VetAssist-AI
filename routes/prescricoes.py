from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload
from database import get_db
from models.prescricao import Prescricao, ItemPrescricao
from models.consulta import Consulta
from schemas.prescricao import (
    PrescricaoCreate,
    PrescricaoUpdate,
    PrescricaoResponse
)
from services.security import (
    obter_usuario_logado,
    exigir_perfil
)

router = APIRouter(
    prefix="/prescricoes",
    tags=["Prescrições"]
)


@router.get("/", response_model=List[PrescricaoResponse])
def listar_prescricoes(
    db: Session = Depends(get_db),
    usuario_logado: str = Depends(obter_usuario_logado)
):
    # joinedload traz os itens aninhados em uma unica consulta
    prescricoes = (
        db.query(Prescricao)
        .options(joinedload(Prescricao.itens))
        .order_by(Prescricao.id.desc())
        .all()
    )
    return prescricoes


@router.post("/", response_model=PrescricaoResponse)
def criar_prescricao(
    prescricao: PrescricaoCreate,
    usuario_logado: str = Depends(obter_usuario_logado),
    db: Session = Depends(get_db)
):
    # Valida existencia da Consulta
    consulta = (
        db.query(Consulta)
        .filter(Consulta.id == prescricao.consulta_id)
        .first()
    )
    if not consulta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Consulta não encontrada."
        )

    if not prescricao.itens or len(prescricao.itens) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A receita deve conter ao menos um medicamento."
        )

    # 1. Cria a Prescricao (Cabecalho da Receita)
    nova_prescricao = Prescricao(
        consulta_id=prescricao.consulta_id,
        observacoes=prescricao.observacoes
    )
    db.add(nova_prescricao)
    db.flush()  # Gera o ID da prescricao antes do commit

    # 2. Insere todos os itens/medicamentos associados incluindo o local de aquisição (tipo_uso)
    for item_data in prescricao.itens:
        novo_item = ItemPrescricao(
            prescricao_id=nova_prescricao.id,
            medicamento=item_data.medicamento,
            dosagem=item_data.dosagem,
            frequencia=item_data.frequencia,
            duracao=item_data.duracao,
            tipo_uso=item_data.tipo_uso,
            observacoes=item_data.observacoes
        )
        db.add(novo_item)

    try:
        db.commit()
        db.refresh(nova_prescricao)
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao registrar receita: {str(e)}"
        )

    return nova_prescricao


@router.get("/completas")
def listar_prescricoes_completas(
    dias: int = 180,
    limite: int = 500,
    db: Session = Depends(get_db),
    usuario_logado: str = Depends(obter_usuario_logado)
):
    """Lista de receitas ja com paciente, tutor, veterinario e itens (para a tela e a reimpressao)."""
    limite = max(1, min(int(limite or 500), 1000))
    filtro_data = ""
    params = {"lim": limite}
    if dias and int(dias) > 0:
        filtro_data = "WHERE p.data_prescricao >= (now() - make_interval(days => :dias))"
        params["dias"] = int(dias)

    linhas = db.execute(text(f"""
        SELECT p.id, p.consulta_id, p.data_prescricao, p.observacoes,
               c.codigo AS consulta_codigo,
               a.id AS animal_id, a.nome AS animal_nome, a.codigo AS animal_codigo,
               a.especie, a.raca, a.peso,
               t.nome AS tutor_nome,
               u.nome AS veterinario_nome, u.crmv AS veterinario_crmv
        FROM prescricao p
        LEFT JOIN consulta c ON c.id = p.consulta_id
        LEFT JOIN animal a ON a.id = c.animal_id
        LEFT JOIN tutor t ON t.id = a.tutor_id
        LEFT JOIN usuario u ON u.id = c.usuario_id
        {filtro_data}
        ORDER BY p.id DESC
        LIMIT :lim
    """), params).mappings().all()

    ids = [l["id"] for l in linhas]
    itens_por = {}
    if ids:
        itens = db.execute(text("""
            SELECT id, prescricao_id, medicamento, dosagem, frequencia, duracao, tipo_uso, observacoes
            FROM item_prescricao WHERE prescricao_id = ANY(:ids) ORDER BY id
        """), {"ids": ids}).mappings().all()
        for i in itens:
            itens_por.setdefault(i["prescricao_id"], []).append(dict(i))

    resultado = []
    for l in linhas:
        obs = l["observacoes"] or ""
        clinica = obs.lower().startswith("aplicações realizadas na clínica") or obs.lower().startswith("aplicacoes realizadas na clinica")
        resultado.append({
            "id": l["id"],
            "consulta_id": l["consulta_id"],
            "consulta_codigo": l["consulta_codigo"],
            "data_prescricao": (l["data_prescricao"].isoformat() + "Z") if l["data_prescricao"] else None,  # gravada em UTC (sem fuso)
            "observacoes": l["observacoes"],
            "tipo": "CLINICA" if clinica else "TUTOR",
            "animal_id": l["animal_id"],
            "animal_nome": l["animal_nome"],
            "animal_codigo": l["animal_codigo"],
            "especie": l["especie"],
            "raca": l["raca"],
            "peso": float(l["peso"]) if l["peso"] is not None else None,
            "tutor_nome": l["tutor_nome"],
            "veterinario_nome": l["veterinario_nome"],
            "veterinario_crmv": l["veterinario_crmv"],
            "itens": itens_por.get(l["id"], []),
        })
    return resultado


@router.get("/{prescricao_id}", response_model=PrescricaoResponse)
def buscar_prescricao(
    prescricao_id: int,
    db: Session = Depends(get_db),
    usuario_logado: str = Depends(obter_usuario_logado)
):
    prescricao = (
        db.query(Prescricao)
        .options(joinedload(Prescricao.itens))
        .filter(Prescricao.id == prescricao_id)
        .first()
    )
    if not prescricao:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prescrição não encontrada."
        )
    return prescricao


@router.put("/{prescricao_id}", response_model=PrescricaoResponse)
def atualizar_prescricao(
    prescricao_id: int,
    prescricao: PrescricaoUpdate,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    prescricao_db = (
        db.query(Prescricao)
        .filter(Prescricao.id == prescricao_id)
        .first()
    )
    if not prescricao_db:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prescrição não encontrada."
        )

    # Atualiza dados do cabecalho se fornecidos
    if prescricao.consulta_id:
        prescricao_db.consulta_id = prescricao.consulta_id
    if prescricao.observacoes is not None:
        prescricao_db.observacoes = prescricao.observacoes

    # Se a lista de itens foi enviada, substitui os itens antigos
    if prescricao.itens is not None:
        # Remove os itens antigos
        db.query(ItemPrescricao).filter(ItemPrescricao.prescricao_id == prescricao_id).delete()
        
        # Recria a lista de itens atualizados com tipo_uso
        for item_data in prescricao.itens:
            novo_item = ItemPrescricao(
                prescricao_id=prescricao_id,
                medicamento=item_data.medicamento,
                dosagem=item_data.dosagem,
                frequencia=item_data.frequencia,
                duracao=item_data.duracao,
                tipo_uso=item_data.tipo_uso,
                observacoes=item_data.observacoes
            )
            db.add(novo_item)

    db.commit()
    db.refresh(prescricao_db)
    return prescricao_db


@router.delete("/{prescricao_id}")
def excluir_prescricao(
    prescricao_id: int,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    prescricao = (
        db.query(Prescricao)
        .filter(Prescricao.id == prescricao_id)
        .first()
    )
    if not prescricao:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prescrição não encontrada."
        )

    db.delete(prescricao)
    db.commit()
    return {"mensagem": "Prescrição e seus itens excluídos com sucesso."}