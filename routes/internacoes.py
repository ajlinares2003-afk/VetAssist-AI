from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime
from database.database import get_db
from models.internacao import Internacao, EvolucaoInternacao
from services.security import obter_usuario_logado, exigir_perfil

# Tenta importar os modelos Animal e Tutor tratando variações de estrutura
try:
    from models.animais import Animal
except ImportError:
    try:
        from models.animal import Animal
    except ImportError:
        from models import Animal

try:
    from models.tutor import Tutor
except ImportError:
    try:
        from models.tutores import Tutor
    except ImportError:
        from models import Tutor

router = APIRouter(
    prefix="/internacoes",
    tags=["Internação & UTI"]
)

class InternacaoCreateSchema(BaseModel):
    animal_id: int
    consulta_id: Optional[int] = None
    leito: str
    nivel_criticidade: str  # ESTAVEL, MODERADO, CRITICO
    motivo: str

class InternacaoUpdateSchema(BaseModel):
    leito: Optional[str] = None
    nivel_criticidade: Optional[str] = None
    status: Optional[str] = None

class EvolucaoCreateSchema(BaseModel):
    temperatura: Optional[float] = None
    freq_cardiaca: Optional[float] = None
    freq_respiratoria: Optional[float] = None
    tpc_segundos: Optional[float] = None
    mucosa: Optional[str] = None
    alimentacao: Optional[str] = None
    dejecoes: Optional[str] = None
    observacoes: Optional[str] = None

@router.get("/")
def listar_internacoes(
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO", "TRIAGEM", "RECEPCAO"])),
    db: Session = Depends(get_db)
):
    resultados = db.query(Internacao, Animal).join(Animal, Internacao.animal_id == Animal.id).filter(Internacao.status == "INTERNADO").all()
    
    lista = []
    for internacao, animal in resultados:
        lista.append({
            "id": internacao.id,
            "animal_id": internacao.animal_id,
            "animal_nome": getattr(animal, 'nome', 'Paciente'),
            "animal_codigo": getattr(animal, 'codigo', f"PET-{animal.id}"),
            "animal_especie": getattr(animal, 'especie', 'Canino'),
            "consulta_id": internacao.consulta_id,
            "leito": internacao.leito,
            "status": internacao.status,
            "nivel_criticidade": internacao.nivel_criticidade,
            "motivo": internacao.motivo,
            "data_entrada": internacao.data_entrada
        })
    return lista

@router.post("/")
def criar_internacao(
    dados: InternacaoCreateSchema,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO", "TRIAGEM"])),
    db: Session = Depends(get_db)
):
    existente = db.query(Internacao).filter(Internacao.animal_id == dados.animal_id, Internacao.status == "INTERNADO").first()
    if existente:
        raise HTTPException(status_code=400, detail="Este paciente já possui uma internação ativa.")

    nova = Internacao(
        animal_id=dados.animal_id,
        consulta_id=dados.consulta_id,
        leito=dados.leito.strip(),
        nivel_criticidade=dados.nivel_criticidade.upper(),
        motivo=dados.motivo.strip(),
        status="INTERNADO"
    )
    db.add(nova)
    db.commit()
    db.refresh(nova)
    return nova

@router.put("/{internacao_id}/alta")
def dar_alta(
    internacao_id: int,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    internacao = db.query(Internacao).filter(Internacao.id == internacao_id).first()
    if not internacao:
        raise HTTPException(status_code=404, detail="Internação não encontrada.")

    internacao.status = "ALTA"
    internacao.data_alta = datetime.now()
    db.commit()
    return {"mensagem": "Alta médica registrada com sucesso!"}

@router.get("/{internacao_id}/evolucoes")
def listar_evolucoes(
    internacao_id: int,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO", "TRIAGEM", "RECEPCAO"])),
    db: Session = Depends(get_db)
):
    return db.query(EvolucaoInternacao).filter(EvolucaoInternacao.internacao_id == internacao_id).order_by(EvolucaoInternacao.data_registro.desc()).all()

@router.post("/{internacao_id}/evolucoes")
def registrar_evolucao(
    internacao_id: int,
    dados: EvolucaoCreateSchema,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO", "TRIAGEM"])),
    db: Session = Depends(get_db)
):
    nova = EvolucaoInternacao(
        internacao_id=internacao_id,
        temperatura=dados.temperatura,
        freq_cardiaca=dados.freq_cardiaca,
        freq_respiratoria=dados.freq_respiratoria,
        tpc_segundos=dados.tpc_segundos,
        mucosa=dados.mucosa,
        alimentacao=dados.alimentacao,
        dejecoes=dados.dejecoes,
        observacoes=dados.observacoes
    )
    db.add(nova)
    db.commit()
    db.refresh(nova)
    return nova

@router.get("/{internacao_id}/boletim")
def gerar_boletim_internacao(
    internacao_id: int,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO", "TRIAGEM", "RECEPCAO"])),
    db: Session = Depends(get_db)
):
    internacao = db.query(Internacao).filter(Internacao.id == internacao_id).first()
    if not internacao:
        raise HTTPException(status_code=404, detail="Internação não encontrada")

    animal = db.query(Animal).filter(Animal.id == internacao.animal_id).first()
    tutor = db.query(Tutor).filter(Tutor.id == animal.tutor_id).first() if (animal and getattr(animal, 'tutor_id', None)) else None

    # Busca a última evolução rigorosamente ordenada pela data mais recente
    evolucoes = db.query(EvolucaoInternacao).filter(
        EvolucaoInternacao.internacao_id == internacao_id
    ).order_by(EvolucaoInternacao.data_registro.desc()).all()

    ultima_evolucao = evolucoes[0] if evolucoes else None
    data_hoje = datetime.now().strftime("%d/%m/%Y às %H:%M")

    # Tratamento seguro para extrair os sinais vitais com fallback para 'N/I'
    temp_str = f"{ultima_evolucao.temperatura} °C" if (ultima_evolucao and getattr(ultima_evolucao, 'temperatura', None) is not None) else "N/I"
    fc_str = f"{ultima_evolucao.freq_cardiaca} bpm" if (ultima_evolucao and getattr(ultima_evolucao, 'freq_cardiaca', None) is not None) else "N/I"
    fr_str = f"{ultima_evolucao.freq_respiratoria} mpm" if (ultima_evolucao and getattr(ultima_evolucao, 'freq_respiratoria', None) is not None) else "N/I"
    
    obs_str = ultima_evolucao.observacoes if (ultima_evolucao and getattr(ultima_evolucao, 'observacoes', None)) else 'Paciente em acompanhamento contínuo na UTI/Internação sem intercorrências graves.'

    texto_whatsapp = f"""🐾 *VetAssist AI — Boletim Diário de Internação* 🐾
📅 *Data:* {data_hoje}

🐶 *Paciente:* {animal.nome if animal else 'N/I'} ({getattr(animal, 'especie', 'Canino')})
👤 *Tutor(a):* {getattr(tutor, 'nome', 'N/I') if tutor else 'N/I'}
🏠 *Leito:* {internacao.leito}
📌 *Status:* {internacao.status}

---
📊 *SINAIS VITAIS & RECENTES:*
• Temp: {temp_str}
• Freq. Cardíaca: {fc_str}
• Freq. Respiratória: {fr_str}
• Nível de Criticidade: {internacao.nivel_criticidade}

📝 *NOTAS DE EVOLUÇÃO / QUADRO CLÍNICO:*
{obs_str}

---
💡 *Qualquer dúvida, nossa equipe veterinária está à disposição!*
"""

    return {
        "internacao_id": internacao.id,
        "animal": animal,
        "tutor": tutor,
        "internacao": internacao,
        "ultima_evolucao": ultima_evolucao,
        "texto_whatsapp": texto_whatsapp,
        "telefone_tutor": getattr(tutor, 'telefone', '') if tutor else ''
    }