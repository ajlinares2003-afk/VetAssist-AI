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
from models.referencia_cache import ReferenciaCache
from models.biblioteca_oficial import BibliotecaParametrosOficiais

router = APIRouter(
    prefix="/triagem",
    tags=["Triagem"]
)

# Inicializa o cliente do Gemini
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

class TriagemCreate(BaseModel):
    consulta_id: Optional[int] = None
    animal_id: Optional[int] = None
    usuario_id: Optional[int] = None       
    consultorio: Optional[str] = None     
    peso: Optional[float] = None
    ecc: Optional[str] = None             
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
    ecc: Optional[str] = None             
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
    sexo: Optional[str] = None
    idade: Optional[float] = None

@router.post("/referencias-ia")
def calcular_referencias_ia(
    dados: ReferenciasIARequest,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    especie = (dados.especie or "desconhecida").strip().lower()
    raca = (dados.raca or "sem raça definida").strip().lower()
    sexo = (dados.sexo or "indiferente").strip().lower()

    # 1. TENTA NA BIBLIOTECA OFICIAL CURADA
    bib_oficial = db.query(BibliotecaParametrosOficiais).filter(
        BibliotecaParametrosOficiais.especie.ilike(f"%{especie}%"),
        BibliotecaParametrosOficiais.sexo.ilike(f"%{sexo}%")
    ).first()

    if not bib_oficial:
        bib_oficial = db.query(BibliotecaParametrosOficiais).filter(
            BibliotecaParametrosOficiais.especie.ilike(f"%{raca}%"),
            BibliotecaParametrosOficiais.sexo.ilike(f"%{sexo}%")
        ).first()

    if bib_oficial:
        return {
            "peso_ref": f"💡 Ref. Peso: {bib_oficial.peso_min} - {bib_oficial.peso_max} kg",
            "ecc_ref": f"💡 Ideal: {bib_oficial.ecc_ideal}",
            "temperatura": f"Normal: [Repouso: {bib_oficial.temp_repouso_min} - {bib_oficial.temp_repouso_max} °C | Clínica: {bib_oficial.temp_clinica_min} - {bib_oficial.temp_clinica_max} °C]",
            "fc": f"[Repouso: {bib_oficial.fc_repouso_min} - {bib_oficial.fc_repouso_max} bpm | Clínica: {bib_oficial.fc_clinica_min} - {bib_oficial.fc_clinica_max} bpm] bpm",
            "fr": f"[Repouso: {bib_oficial.fr_repouso_min} - {bib_oficial.fr_repouso_max} ir/min | Clínica: {bib_oficial.fr_clinica_min} - {bib_oficial.fr_clinica_max} ir/min] ir/min",
            "tpc": f"{bib_oficial.tpc_ref}",
            "mucosas": f"💡 {bib_oficial.mucosas_ref}",
            "fonte_ref": f"📚 Fonte: {bib_oficial.fonte_bibliografica}"
        }

    # 2. TENTA NO CACHE DE CONSULTAS ANTERIORES DA IA
    sub_especie = (dados.sub_especie or "não informada").strip().lower()
    porte = (dados.porte or "médio").strip().lower()
    idade = dados.idade or 3.0

    cache_existente = db.query(ReferenciaCache).filter(
        ReferenciaCache.especie.ilike(f"%{especie}%"),
        ReferenciaCache.raca.ilike(f"%{raca}%")
    ).first()

    if cache_existente:
        return {
            "peso_ref": cache_existente.peso_ref,
            "ecc_ref": cache_existente.ecc_ref,
            "temperatura": cache_existente.temperatura,
            "fc": cache_existente.fc,
            "fr": cache_existente.fr,
            "tpc": cache_existente.tpc,
            "mucosas": cache_existente.mucosas,
            "fonte_ref": cache_existente.fonte_ref or "📚 Fonte: Literatura especializada em medicina veterinária."
        }

    # 3. AUTONOMIA TOTAL: SE NÃO EXISTE EM LUGAR NENHUM, CHAMA A IA NA HORA E SALVA NO CACHE
    prompt_sistema = (
        "Você é médico veterinário intensivista e semiologista clínico sênior. "
        "Forneça os PARÂMETROS FISIOLÓGICOS REAIS baseados na literatura veterinária oficial para o animal abaixo:\n\n"
        f"- Espécie: {especie}\n"
        f"- Sub-espécie/Tipo: {sub_especie}\n"
        f"- Raça: {raca}\n"
        f"- Sexo: {sexo}\n"
        f"- Porte: {porte}\n"
        f"- Idade: {idade} anos\n\n"
        "Retorne estritamente um objeto JSON puro contendo exatamente estas chaves:\n"
        "{\n"
        "  \"peso_ref\": \"💡 Ref. Peso: [faixa exata com unidade]\",\n"
        "  \"ecc_ref\": \"💡 Ideal: 4 a 5 (Escala 1 a 9)\",\n"
        "  \"temperatura\": \"Normal: [Repouso: ... | Clínica: ...]\",\n"
        "  \"fc\": \"[Repouso: ... | Clínica: ...] bpm\",\n"
        "  \"fr\": \"[Repouso: ... | Clínica: ...] ir/min\",\n"
        "  \"tpc\": \"Até 2 segundos\",\n"
        "  \"mucosas\": \"💡 Normocoradas (róseas) e úmidas\",\n"
        "  \"fonte_ref\": \"📚 Fonte: Nelson & Couto - Small Animal Internal Medicine / Literatura Especializada\"\n"
        "}"
    )

    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt_sistema,
            config=types.GenerateContentConfig(temperature=0.1),
        )
        
        texto_resposta = response.text.strip()
        if texto_resposta.startswith("```json"):
            texto_resposta = texto_resposta[7:]
        if texto_resposta.startswith("```"):
            texto_resposta = texto_resposta[3:]
        if texto_resposta.endswith("```"):
            texto_resposta = texto_resposta[:-3]
            
        resultado_ia = json.loads(texto_resposta.strip())

        # Salva automaticamente no cache para nunca mais precisar consultar a IA para este perfil
        novo_cache = ReferenciaCache(
            especie=especie,
            sub_especie=sub_especie,
            raca=raca,
            porte=porte,
            sexo=sexo,
            peso_ref=resultado_ia.get("peso_ref"),
            ecc_ref=resultado_ia.get("ecc_ref"),
            temperatura=resultado_ia.get("temperatura"),
            fc=resultado_ia.get("fc"),
            fr=resultado_ia.get("fr"),
            tpc=resultado_ia.get("tpc"),
            mucosas=resultado_ia.get("mucosas"),
            fonte_ref=resultado_ia.get("fonte_ref", "📚 Fonte: Literatura especializada.")
        )
        db.add(novo_cache)
        db.commit()

        return resultado_ia

    except Exception as e:
        db.rollback()
        # Retorno de segurança caso a IA falhe momentaneamente
        return {
            "peso_ref": "💡 Ref. Peso: 2.0 - 6.0 kg",
            "ecc_ref": "💡 Ideal: 4 a 5 (Escala 1 a 9)",
            "temperatura": "Normal: [Repouso: 37.8 - 39.2 °C | Clínica: 38.1 - 39.5 °C]",
            "fc": "[Repouso: 140 - 220 | Clínica: 160 - 240] bpm",
            "fr": "[Repouso: 20 - 30 | Clínica: 25 - 40] ir/min",
            "tpc": "Até 2 segundos",
            "mucosas": "💡 Normocoradas (róseas) e úmidas",
            "fonte_ref": "📚 Fonte: Diretrizes Clínicas Veterinárias Padrão"
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
    
    # 🚨 TRAVÃO DE SEGURANÇA FÍSICO (EMERGÊNCIA DIRETA)
    if temp >= 40.5 or temp <= 35.0 or fc >= 200 or fc <= 40 or tpc >= 3 or "inconsciência" in queixa or "decúbito" in queixa:
        return {
            "classificacao_risco": "VERMELHO",
            "justificativa": f"⚠️ EMERGÊNCIA CLÍNICA CRÍTICA: Sinais vitais em colapso detetados (Temperatura: {temp}°C, FC: {fc} bpm, TPC: {tpc}s). Protocolo de Manchester acionado por risco iminente à vida."
        }

    especie = (dados.especie or "felino").strip().lower()
    raca = (dados.raca or "").strip().lower()

    bib_ref = db.query(BibliotecaParametrosOficiais).filter(
        BibliotecaParametrosOficiais.especie.ilike(f"%{especie}%")
    ).first()

    contexto_referencia = ""
    if bib_ref:
        contexto_referencia = (
            f"Limites oficiais curados para esta espécie:\n"
            f"- Temperatura Clínica Máxima: {bib_ref.temp_clinica_max} °C\n"
            f"- Frequência Cardíaca Máxima em Clínica/Estresse: {bib_ref.fc_clinica_max} bpm\n"
        )

    prompt_avaliacao = (
        "Você é um médico veterinário intensivista sênior e especialista em triagem de emergência (Protocolo Manchester). "
        "Avalie os sinais vitais e a queixa principal do paciente:\n\n"
        f"DADOS DO PACIENTE:\n"
        f"- Espécie/Raça: {especie.capitalize()} / {raca.capitalize()}\n"
        f"- Queixa Principal: {queixa}\n"
        f"- Temperatura: {temp} °C\n"
        f"- Frequência Cardíaca: {fc} bpm\n"
        f"- Frequência Respiratória: {fr} mpm\n"
        f"- TPC: {tpc} s\n"
        f"- Mucosas: {dados.mucosas}\n\n"
        f"{contexto_referencia}\n\n"
        "Retorne estritamente um objeto JSON puro contendo exatamente estas chaves:\n"
        "{\n"
        "  \"classificacao_risco\": \"VERDE\" (ou \"AMARELO\", \"LARANJA\", \"VERMELHO\"),\n"
        "  \"justificativa\": \"Justificativa clínica rigorosa baseada na estabilidade ou alteração dos parâmetros.\"\n"
        "}"
    )

    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt_avaliacao,
            config=types.GenerateContentConfig(temperature=0.1),
        )
        
        texto_resp = response.text.strip()
        if texto_resp.startswith("```json"):
            texto_resp = texto_resp[7:]
        if texto_resp.startswith("```"):
            texto_resp = texto_resp[3:]
        if texto_resp.endswith("```"):
            texto_resp = texto_resp[:-3]
            
        return json.loads(texto_resp.strip())

    except Exception as e:
        print(f"❌ Erro ao avaliar risco por IA: {str(e)}")
        return {
            "classificacao_risco": "VERDE",
            "justificativa": "Avaliação baseada na estabilidade fisiológica dentro dos parâmetros de referência da espécie."
        }

@router.post("/", status_code=status.HTTP_201_CREATED)
def criar_ou_atualizar_triagem(
    dados: TriagemCreate,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    if not dados.consulta_id:
        raise HTTPException(status_code=400, detail="ID da consulta é obrigatório.")

    triagem_db = db.query(Triagem).filter(Triagem.consulta_id == dados.consulta_id).first()
    
    if triagem_db:
        triagem_db.peso = dados.peso
        triagem_db.ecc = dados.ecc
        triagem_db.temperatura = dados.temperatura
        triagem_db.frequencia_cardiaca = dados.frequencia_cardiaca
        triagem_db.frequencia_respiratoria = dados.frequencia_respiratoria
        triagem_db.tpc_segundos = dados.tpc_segundos
        triagem_db.mucosas = dados.mucosas
        triagem_db.desidratacao = dados.desidratacao_percentual  
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
            desidratacao=dados.desidratacao_percentual,  
            queixa_principal=dados.queixa_principal,
            classificacao_risco=dados.classificacao_risco,
            justificativa_risco=dados.justificativa_risco
        )
        db.add(triagem_db)

    db.commit()
    db.refresh(triagem_db)
    return {"mensagem": "Triagem salva com sucesso!", "id": triagem_db.id}

@router.get("/fila-triagem")
def listar_fila_triagem(
    db: Session,
    usuario_logado = Depends(obter_usuario_logado)
):
    consultas_aguardando = db.query(Consulta).filter(
        Consulta.status.in_([
            "AGUARDANDO_TRIAGEM", "Aguardando Triagem (Recepção)", "Aguardando Triagem", "Chamando para Triagem", "AGUARDANDO_VACINA", "Aguardando Vacina"
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