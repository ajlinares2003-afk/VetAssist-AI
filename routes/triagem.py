import os
import json
from google import genai
from google.genai import types
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database.database import get_db
from models.consulta import Consulta
from models.animais import Animal
from models.usuario import Usuario
from services.security import obter_usuario_logado
from pydantic import BaseModel
from typing import Optional
from models.triagem import Triagem 

router = APIRouter(
    prefix="/triagem",
    tags=["Triagem"]
)

# Inicializa o cliente do Gemini
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

class TriagemCreate(BaseModel):
    consulta_id: Optional[int] = None
    animal_id: Optional[int] = None
    usuario_id: Optional[int] = None       # Vínculo do Veterinário Responsável
    consultorio: Optional[str] = None     # Consultório Atribuído
    peso: Optional[float] = None
    ecc: Optional[str] = None             # Escore de Condição Corporal
    temperatura: Optional[float] = None
    frequencia_cardiaca: Optional[int] = None
    frequencia_respiratoria: Optional[int] = None
    tpc_segundos: Optional[int] = None
    mucosas: Optional[str] = "Normocoradas"
    desidratacao_percentual: Optional[int] = None
    queixa_principal: str
    classificacao_risco: str
    justificativa_risco: Optional[str] = None

class AvaliacaoIARequest(BaseModel):
    animal_id: Optional[int] = None
    especie: Optional[str] = "Felino"
    sub_especie: Optional[str] = None
    raca: Optional[str] = None
    ecc: Optional[str] = None             # Escore de Condição Corporal para a IA avaliar
    queixa_principal: str
    temperatura: Optional[float] = None
    frequencia_cardiaca: Optional[int] = None
    frequencia_respiratoria: Optional[int] = None
    tpc_segundos: Optional[int] = None
    mucosas: Optional[str] = "Normocoradas"

class ReferenciasIARequest(BaseModel):
    especie: Optional[str] = "Felino"
    sub_especie: Optional[str] = None
    raca: Optional[str] = None
    porte: Optional[str] = None
    idade: Optional[float] = None

@router.post("/referencias-ia")
def calcular_referencias_ia(
    dados: ReferenciasIARequest,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    # Captura os dados enviados pelo payload de forma segura dentro da função
    especie = dados.especie or "Desconhecida"
    sub_especie = dados.sub_especie or "Não informada"
    raca = dados.raca or "Sem raça definida"
    porte = dados.porte or "Médio"
    idade = dados.idade or 3.0

    # O prompt agora fica dentro da função, tendo acesso às variáveis locais
    prompt_sistema = (
        "Você é um médico veterinário intensivista e semiologista clínico sênior, especialista em fisiologia "
        "de pequenos, grandes animais, pets não convencionais, exóticos, animais silvestres e de zoológico. "
        "Sua tarefa é retornar estritamente um objeto JSON puro (sem blocos de código markdown ou texto adicional) contendo "
        "as faixas de referência fisiológica e de escore de condição corporal (ECC escala 1 a 9) corretas para o paciente descrito abaixo.\n\n"
        f"Dados do Paciente:\n"
        f"- Espécie: {especie}\n"
        f"- Sub-espécie/Tipo: {sub_especie}\n"
        f"- Raça/Variedade: {raca}\n"
        f"- Porte: {porte}\n"
        f"- Idade: {idade} anos\n\n"
        "REGRAS OBRIGATÓRIAS PARA O JSON:\n"
        "1. A chave 'peso_ref' DEVE começar exatamente com '💡 Ref. Peso: ' seguido de uma faixa numérica válida no formato 'X.X - Y.X kg' (Exemplo: '💡 Ref. Peso: 8.0 - 18.0 kg (Caracal adulto)').\n"
        "2. Retorne APENAS o JSON com as chaves: peso_ref, ecc_ref, temperatura, fc, fr, tpc, mucosas.\n\n"
        "O JSON retornado deve conter exatamente estas chaves:\n"
        "{\n"
        "  \"peso_ref\": \"...\",\n"
        "  \"ecc_ref\": \"...\",\n"
        "  \"temperatura\": \"...\",\n"
        "  \"fc\": \"...\",\n"
        "  \"fr\": \"...\",\n"
        "  \"tpc\": \"...\",\n"
        "  \"mucosas\": \"...\"\n"
        "}"
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt_sistema,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1
            ),
        )
        
        resultado_ia = json.loads(response.text)
        return resultado_ia

    except Exception as e:
        print(f"Erro ao consultar IA para referências: {e}")
        return {
            "peso_ref": f"💡 Ref. Peso: 3.0 - 6.0 kg",
            "ecc_ref": "💡 Ideal: 4 a 5 (Escala 1 a 9)",
            "temperatura": "Normal: 38.0°C - 39.2°C",
            "fc": "70 - 160 bpm",
            "fr": "15 - 30 mpm",
            "tpc": "Até 2s",
            "mucosas": "💡 Normocoradas (Rosadas e úmidas)"
        }

@router.post("/avaliar-ia")
def avaliar_triagem_ia(
    dados: AvaliacaoIARequest,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    temp = dados.temperatura or 38.5
    fc = dados.frequencia_cardiaca or 120
    fr = dados.frequencia_respiratoria or 20
    tpc = dados.tpc_segundos or 2
    queixa = (dados.queixa_principal or "").lower()
    especie = (dados.especie or "").lower()
    sub_especie = (dados.sub_especie or "").lower()
    raca = (dados.raca or "").lower()

    termos_graves = ["anorexia", "inchaço", "mandíbula", "sangue", "convulsão", "choque", "apático", "prostrado", "fratura", "parou"]
    tem_termo_grave = any(termo in queixa for termo in termos_graves)

    # Identificação inteligente genérica baseada em parâmetros críticos e termos graves
    if temp > 41.5 or temp < 32.0 or tpc > 4 or tem_termo_grave:
        return {
            "classificacao_risco": "LARANJA",
            "justificativa": f"Parâmetros vitais críticos ou queixa de alerta detectada para a espécie ({especie.capitalize()} / {raca}). Requer atenção imediata."
        }
    elif temp > 39.5 or fc > 180 or dados.mucosas in ["Cianóticas", "Hipocoradas / Pálidas"]:
        return {
            "classificacao_risco": "AMARELO",
            "justificativa": "Sinais vitais moderadamente alterados ou alterações sistêmicas observadas."
        }
    else:
        return {
            "classificacao_risco": "VERDE",
            "justificativa": f"Parâmetros fisiológicos e queixa clínica estáveis dentro da normalidade para {especie.capitalize()}."
        }

@router.post("/", status_code=status.HTTP_201_CREATED)
def criar_ou_atualizar_triagem(
    dados: TriagemCreate,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    if not dados.consulta_id:
        raise HTTPException(status_code=400, detail="ID da consulta é obrigatório para esta operação.")

    triagem_db = db.query(Triagem).filter(Triagem.consulta_id == dados.consulta_id).first()
    
    if triagem_db:
        triagem_db.peso = dados.peso
        triagem_db.ecc = dados.ecc
        triagem_db.temperatura = dados.temperatura
        triagem_db.frequencia_cardiaca = dados.frequencia_cardiaca
        triagem_db.frequencia_respiratoria = dados.frequencia_respiratoria
        triagem_db.tpc_segundos = dados.tpc_segundos
        triagem_db.mucosas = dados.mucosas
        triagem_db.desidratacao = dados.desidratacao_percentual  # Mapeado para a coluna 'desidratacao' do banco
        triagem_db.queixa_principal = dados.queixa_principal
        triagem_db.classificacao_risco = dados.classificacao_risco
        triagem_db.justificativa_risco = dados.justificativa_risco
    else:
        triagem_db = Triagem(
            consulta_id=dados.consulta_id,
            peso=dados.peso,
            ecc=dados.ecc,
            temperatura=dados.temperatura,
            frequencia_cardiaca=dados.frequencia_cardiaca,
            frequencia_respiratoria=dados.frequencia_respiratoria,
            tpc_segundos=dados.tpc_segundos,
            mucosas=dados.mucosas,
            desidratacao=dados.desidratacao_percentual,  # Mapeado para a coluna 'desidratacao' do banco
            queixa_principal=dados.queixa_principal,
            classificacao_risco=dados.classificacao_risco,
            justificativa_risco=dados.justificativa_risco
        )
        db.add(triagem_db)

    consulta = db.query(Consulta).filter(Consulta.id == dados.consulta_id).first()
    if consulta:
        consulta.status = "Aguardando Consulta (Fila Vet)"
        if dados.usuario_id:
            consulta.usuario_id = dados.usuario_id
        if dados.consultorio:
            consulta.consultorio = dados.consultorio
        if dados.peso:
            consulta.peso_atendimento = dados.peso
        if dados.temperatura:
            consulta.temperatura = dados.temperatura
        if dados.frequencia_cardiaca:
            consulta.frequencia_cardiaca = dados.frequencia_cardiaca
        if dados.frequencia_respiratoria:
            consulta.frequencia_respiratoria = dados.frequencia_respiratoria

    db.commit()
    db.refresh(triagem_db)
    return {"mensagem": "Triagem salva com sucesso!", "id": triagem_db.id}

@router.post("/checkin-direto", status_code=status.HTTP_201_CREATED)
def criar_checkin_triagem_direto(
    dados: TriagemCreate,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    if not dados.animal_id:
        raise HTTPException(status_code=400, detail="O ID do animal é obrigatório para o check-in direto.")

    try:
        user_id = dados.usuario_id
        if not user_id:
            if isinstance(usuario_logado, dict):
                email_busca = usuario_logado.get("sub") or usuario_logado.get("email")
                if email_busca:
                    u_db = db.query(Usuario).filter(Usuario.email == email_busca).first()
                    if u_db:
                        user_id = u_db.id
            else:
                user_id = getattr(usuario_logado, "id", None)

        if not user_id:
            primeiro_usuario = db.query(Usuario).first()
            if primeiro_usuario:
                user_id = primeiro_usuario.id

        nova_consulta = Consulta(
            animal_id=dados.animal_id,
            usuario_id=user_id,
            consultorio=dados.consultorio,
            queixa_principal=dados.queixa_principal,
            status="Aguardando Consulta (Fila Vet)",
            peso_atendimento=dados.peso,
            temperatura=dados.temperatura,
            frequencia_cardiaca=dados.frequencia_cardiaca,
            frequencia_respiratoria=dados.frequencia_respiratoria
        )
        db.add(nova_consulta)
        db.commit()
        db.refresh(nova_consulta)

        if not nova_consulta.codigo:
            nova_consulta.codigo = f"CNS-{nova_consulta.id:04d}"
            db.commit()

        triagem_db = Triagem(
            consulta_id=nova_consulta.id,
            peso=dados.peso,
            ecc=dados.ecc,
            temperatura=dados.temperatura,
            frequencia_cardiaca=dados.frequencia_cardiaca,
            frequencia_respiratoria=dados.frequencia_respiratoria,
            tpc_segundos=dados.tpc_segundos,
            mucosas=dados.mucosas,
            desidratacao=dados.desidratacao_percentual,
            queixa_principal=dados.queixa_principal,
            classificacao_risco=dados.classificacao_risco,
            justificativa_risco=dados.justificativa_risco
        )
        db.add(triagem_db)

        if dados.peso:
            animal = db.query(Animal).filter(Animal.id == dados.animal_id).first()
            if animal:
                animal.peso = dados.peso

        db.commit()
        return {"mensagem": "Check-in e Triagem Direta realizados com sucesso!", "consulta_id": nova_consulta.id}
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Erro ao salvar no banco: {str(e)}")

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
            "Chamando para Triagem",
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
            "peso_atendimento": getattr(c, 'peso_atendimento', None),
            "temperatura": c.temperatura,
            "frequencia_cardiaca": c.frequencia_cardiaca,
            "frequencia_respiratoria": c.frequencia_respiratoria,
            "status": c.status
        })
    return resultado

@router.get("/consulta/{consulta_id}")
def buscar_triagem_por_consulta(
    consulta_id: int,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    triagem_db = db.query(Triagem).filter(Triagem.consulta_id == consulta_id).first()
    
    if triagem_db:
        return {
            "consulta_id": triagem_db.consulta_id,
            "peso": triagem_db.peso,
            "ecc": getattr(triagem_db, 'ecc', None),
            "temperatura": triagem_db.temperatura,
            "frequencia_cardiaca": triagem_db.frequencia_cardiaca,
            "frequencia_respiratoria": triagem_db.frequencia_respiratoria,
            "tpc_segundos": triagem_db.tpc_segundos,
            "mucosas": triagem_db.mucosas,
            "queixa_principal": triagem_db.queixa_principal,
            "observacoes": getattr(triagem_db, 'observacoes', "")
        }

    consulta = db.query(Consulta).filter(Consulta.id == consulta_id).first()
    if not consulta:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    
    return {
        "consulta_id": consulta.id,
        "peso": getattr(consulta, 'peso_atendimento', None),
        "ecc": None,
        "temperatura": consulta.temperatura,
        "frequencia_cardiaca": consulta.frequencia_cardiaca,
        "frequencia_respiratoria": consulta.frequencia_respiratoria,
        "tpc_segundos": getattr(consulta, 'tpc_segundos', 2),
        "mucosas": getattr(consulta, 'mucosas', "Normocoradas"),
        "observacoes": consulta.observacoes or ""
    }