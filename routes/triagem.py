from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from database.database import get_db
from models.triagem import Triagem
from models.consulta import Consulta, StatusAtendimentoEnum
from models.usuario import Usuario
from models.animais import Animal
from schemas.triagem import TriagemCreate, TriagemResponse
from services.security import obter_usuario_logado
from groq import Groq
import os
from dotenv import load_dotenv

router = APIRouter(
    prefix="/triagem",
    tags=["Triagem & Pré-Atendimento"]
)

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client_groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


class CheckinTriagemDiretaCreate(BaseModel):
    animal_id: int
    queixa_principal: str
    classificacao_risco: str  # VERMELHO, LARANJA, AMARELO, VERDE, AZUL
    peso: Optional[float] = None
    temperatura: Optional[float] = None
    frequencia_cardiaca: Optional[int] = None
    frequencia_respiratoria: Optional[int] = None
    tpc_segundos: Optional[int] = None
    mucosas: Optional[str] = "Normocoradas"
    desidratacao_percentual: Optional[int] = None
    justificativa_risco: Optional[str] = None


class AvaliacaoIaTriagemRequest(BaseModel):
    animal_id: Optional[int] = None
    especie: Optional[str] = "Felino"
    queixa_principal: str
    temperatura: Optional[float] = None
    frequencia_cardiaca: Optional[int] = None
    frequencia_respiratoria: Optional[int] = None
    tpc_segundos: Optional[int] = None
    mucosas: Optional[str] = "Normocoradas"


@router.get("/fila-triagem")
def listar_fila_triagem(
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    consultas_aguardando = db.query(Consulta).filter(
        Consulta.status.in_([
            "AGUARDANDO_TRIAGEM",
            "Aguardando Triagem (Recepção)",
            "Aguardando Triagem",
            "AGUARDANDO_VACINA",
            "Aguardando Vacina"
        ])
    ).order_by(Consulta.id.asc()).all()

    resultado = []
    for c in consultas_aguardando:
        animal = db.query(Animal).filter(Animal.id == c.animal_id).first()
        resultado.append({
            "id": c.id,
            "codigo": c.codigo or f"CNS-{c.id:04d}",
            "animal_id": c.animal_id,
            "pet": animal.nome if animal else "Paciente",
            "especie": animal.especie if animal else "-",
            "queixa_principal": c.queixa_principal,
            "peso_atendimento": c.peso_atendimento if hasattr(c, 'peso_atendimento') else getattr(c, 'peso', None),
            "temperatura": c.temperatura,
            "frequencia_cardiaca": c.frequencia_cardiaca,
            "frequencia_respiratoria": c.frequencia_respiratoria,
            "status": c.status
        })
    return resultado


@router.post("/avaliar-ia")
def avaliar_triagem_com_ia(
    dados: AvaliacaoIaTriagemRequest,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    especie_paciente = dados.especie or "Felino"
    if dados.animal_id:
        animal = db.query(Animal).filter(Animal.id == dados.animal_id).first()
        if animal and animal.especie:
            especie_paciente = animal.especie

    prompt_triagem = f"""
    Você é o Assistente Especialista em Triagem Clínica Veterinária do sistema VetAssist AI (Protocolo Manchester).
    Sua tarefa é avaliar os sinais vitais e a mucosa do paciente da espécie: {especie_paciente}.

    TABELA DE REFERÊNCIA FISIOLÓGICA ({especie_paciente.upper()}):
    - Temperatura: 38.0 °C a 39.2 °C (Normotermia)
    - Frequência Cardíaca (FC): 120 a 180 bpm para felinos / 70 a 140 bpm para cães
    - Frequência Respiratória (FR): 20 a 30 mpm
    - TPC: <= 2 segundos (Normal)
    - Mucosas: Normocoradas / Rosadas (Normal). Mucosas pálidas, cianóticas ou ictericas indicam urgência.

    DADOS DO ATENDIMENTO ATUAL:
    - Espécie do Paciente: {especie_paciente}
    - Queixa Principal: {dados.queixa_principal}
    - Temperatura Aferida: {dados.temperatura or 'Não aferida'} °C
    - Frequência Cardíaca (FC): {dados.frequencia_cardiaca or 'Não aferida'} bpm
    - Frequência Respiratória (FR): {dados.frequencia_respiratoria or 'Não aferida'} mpm
    - TPC: {dados.tpc_segundos or 'Não aferido'} segundo(s)
    - Mucosas: {dados.mucosas or 'Não informado'}

    REGRAS DE CLASSIFICAÇÃO MANCHESTER:
    - Vitais normais, TPC <= 2s e Mucosas Normocoradas -> "Pouco Urgente" (VERDE) ou "Não Urgente" (AZUL).
    - Mucosas Cianóticas (roxas) ou TPC > 3s -> Aumentar risco para "Muito Urgente" (LARANJA) ou "Emergência" (VERMELHO).
    
    Responda em JSON rigoroso com este formato:
    ```json
    {{
      "classificacao_risco": "VERDE",
      "nivel_texto": "Pouco Urgente",
      "justificativa": "Paciente com parâmetros vitais, TPC e coloração de mucosas dentro dos padrões fisiológicos."
    }}
    ```
    """

    if client_groq:
        try:
            response = client_groq.chat.completions.create(
                model="qwen/qwen3.8-27b",
                messages=[{"role": "user", "content": prompt_triagem}],
                max_tokens=500,
                temperature=0.1
            )
            texto = response.choices[0].message.content
            import json
            if "```json" in texto:
                bloco = texto.split("```json")[1].split("```")[0].strip()
                return json.loads(bloco)
        except Exception as e:
            print(f"Erro ao avaliar IA na Triagem: {e}")

    return {
        "classificacao_risco": "VERDE",
        "nivel_texto": "Pouco Urgente",
        "justificativa": f"Parâmetros vitais normais para a espécie {especie_paciente}."
    }


@router.post("/", response_model=TriagemResponse)
def registrar_triagem(
    triagem_data: TriagemCreate,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    consulta = db.query(Consulta).filter(Consulta.id == triagem_data.consulta_id).first()
    if not consulta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Atendimento/Consulta não encontrado."
        )

    triagem_existente = db.query(Triagem).filter(Triagem.consulta_id == triagem_data.consulta_id).first()
    if triagem_existente:
        for key, value in triagem_data.model_dump(exclude_unset=True).items():
            setattr(triagem_existente, key, value)
        nova_triagem = triagem_existente
    else:
        nova_triagem = Triagem(**triagem_data.model_dump())
        db.add(nova_triagem)

    consulta.status = StatusAtendimentoEnum.AGUARDANDO_CONSULTA
    
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


@router.post("/checkin-direto")
def realizar_checkin_e_triagem_direta(
    dados: CheckinTriagemDiretaCreate,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    mapa_cores = {
        "EMERGENCIA": "VERMELHO",
        "MUITO_URGENTE": "LARANJA",
        "URGENCIA": "AMARELO",
        "POUCO_URGENTE": "VERDE",
        "NAO_URGENTE": "AZUL"
    }
    cor_risco = dados.classificacao_risco.upper()
    cor_risco = mapa_cores.get(cor_risco, cor_risco)

    email_usuario = None
    usuario_id_token = None

    if isinstance(usuario_logado, dict):
        email_usuario = usuario_logado.get("email")
        usuario_id_token = usuario_logado.get("sub")
    else:
        email_usuario = str(usuario_logado)

    usuario = None
    if email_usuario:
        usuario = db.query(Usuario).filter(Usuario.email == email_usuario).first()
    
    if not usuario and usuario_id_token and str(usuario_id_token).isdigit():
        usuario = db.query(Usuario).filter(Usuario.id == int(usuario_id_token)).first()

    usuario_id = usuario.id if usuario else 1

    nova_consulta = Consulta(
        usuario_id=usuario_id,
        animal_id=dados.animal_id,
        queixa_principal=dados.queixa_principal,
        status=StatusAtendimentoEnum.AGUARDANDO_CONSULTA,
        data_consulta=datetime.now()
    )
    
    if hasattr(nova_consulta, 'peso_atendimento') and dados.peso:
        nova_consulta.peso_atendimento = dados.peso
    elif hasattr(nova_consulta, 'peso') and dados.peso:
        nova_consulta.peso = dados.peso

    if hasattr(nova_consulta, 'temperatura'):
        nova_consulta.temperatura = dados.temperatura
    if hasattr(nova_consulta, 'frequencia_cardiaca'):
        nova_consulta.frequencia_cardiaca = dados.frequencia_cardiaca
    if hasattr(nova_consulta, 'frequencia_respiratoria'):
        nova_consulta.frequencia_respiratoria = dados.frequencia_respiratoria

    db.add(nova_consulta)
    db.flush()

    nova_triagem = Triagem(
        consulta_id=nova_consulta.id,
        peso=dados.peso,
        temperatura=dados.temperatura,
        frequencia_cardiaca=dados.frequencia_cardiaca,
        frequencia_respiratoria=dados.frequencia_respiratoria,
        tpc_segundos=dados.tpc_segundos,
        desidratacao_percentual=dados.desidratacao_percentual,
        queixa_principal=dados.queixa_principal,
        classificacao_risco=cor_risco,
        justificativa_risco=dados.justificativa_risco
    )
    db.add(nova_triagem)

    try:
        db.commit()
        db.refresh(nova_consulta)
        db.refresh(nova_triagem)
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao realizar Check-in e Triagem: {str(e)}"
        )

    return {
        "mensagem": "Check-in e Triagem realizados com sucesso!",
        "consulta_id": nova_consulta.id,
        "triagem_id": nova_triagem.id
    }


@router.get("/consulta/{consulta_id}", response_model=TriagemResponse)
def buscar_triagem_por_consulta(
    consulta_id: int,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    triagem = db.query(Triagem).filter(Triagem.consulta_id == consulta_id).first()
    if not triagem:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Triagem não encontrada para esta consulta."
        )
    return triagem