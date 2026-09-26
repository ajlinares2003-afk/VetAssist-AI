import os
from datetime import date, timedelta
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from database.database import get_db, SessionLocal
from models.tutor import Tutor
from models.animais import Animal
from models.consulta import Consulta
from models.exame import Exame
from models.vacina import Vacina
from models.prescricao import Prescricao
from models.internacao import Internacao
from models.agendamento import Agendamento
from models.usuario import Usuario
from services.security import exigir_perfil, obter_usuario_logado
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"]
)

def obter_config_ia_dinamica(provedor_desejado: str = "groq_1"):
    db = SessionLocal()
    try:
        if "groq" in provedor_desejado:
            chave_nome = "groq_api_key_2" if "2" in provedor_desejado else "groq_api_key_1"
            modelo_chave = "groq_model_2" if "2" in provedor_desejado else "groq_model_1"
            
            r_mod = db.execute(text("SELECT valor FROM configuracoes_sistema WHERE chave = :c"), {"c": modelo_chave}).fetchone()
            r_key = db.execute(text("SELECT valor FROM configuracoes_sistema WHERE chave = :c"), {"c": chave_nome}).fetchone()
            
            modelo = r_mod[0] if r_mod and r_mod[0] else ("qwen/qwen3.8-27b" if "2" in provedor_desejado else "openai/gpt-oss-120b")
            key_db = r_key[0] if r_key and r_key[0] and not str(r_key[0]).startswith("****") else None
            api_key = key_db or os.getenv("GROQ_API_KEY")
            
            client = Groq(api_key=api_key) if api_key else None
            return client, modelo
        else:
            r_mod = db.execute(text("SELECT valor FROM configuracoes_sistema WHERE chave = 'gemini_model'")).fetchone()
            r_key = db.execute(text("SELECT valor FROM configuracoes_sistema WHERE chave = 'gemini_api_key'")).fetchone()
            
            modelo = r_mod[0] if r_mod and r_mod[0] else "gemini-3.6-flash"
            key_db = r_key[0] if r_key and r_key[0] and not str(r_key[0]).startswith("****") else None
            api_key = key_db or os.getenv("GEMINI_API_KEY_PRIMARY") or os.getenv("GEMINI_API_KEY")
            return api_key, modelo
    finally:
        db.close()


class ResumoDiaRequest(BaseModel):
    perfil: Optional[str] = "RECEPCAO"


class MensagemIaRequest(BaseModel):
    mensagem: str
    perfil: Optional[str] = "RECEPCAO"


@router.get("/metricas")
def obter_metricas_dashboard(
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO", "TRIAGEM", "RECEPCAO"])),
    db: Session = Depends(get_db)
):
    hoje = date.today()

    total_pacientes = db.query(Animal).count()
    consultas_hoje = db.query(Consulta).filter(func.date(Consulta.data_consulta) == hoje).count()
    
    agendamentos_hoje_query = db.query(Agendamento).filter(func.date(Agendamento.data_horario) == hoje).order_by(Agendamento.data_horario.asc()).all()
    total_agendamentos = len(agendamentos_hoje_query)

    lista_agendamentos_hoje = []
    for ag in agendamentos_hoje_query:
        animal = db.query(Animal).filter(Animal.id == ag.animal_id).first()
        vet = db.query(Usuario).filter(Usuario.id == ag.usuario_id).first() if ag.usuario_id else None
        
        nome_tutor = "-"
        if animal:
            if hasattr(animal, 'tutor') and animal.tutor:
                nome_tutor = animal.tutor.nome

        lista_agendamentos_hoje.append({
            "id": ag.id,
            "hora": ag.data_horario.strftime("%H:%M"),
            "pet_nome": animal.nome if animal else "Paciente",
            "especie": animal.especie if animal else "-",
            "tutor_nome": nome_tutor,
            "veterinario_nome": vet.nome if vet else "Qualquer Veterinário",
            "tipo_servico": ag.tipo_servico,
            "status": ag.status
        })

    leitos_totais = 10
    internados = db.query(Internacao).filter(Internacao.status == "INTERNADO").all()
    total_internados = len(internados)
    
    taxa_ocupacao = round((total_internados / leitos_totais) * 100, 1) if leitos_totais > 0 else 0
    leitos_disponiveis = max(0, leitos_totais - total_internados)

    pacientes_criticos = []
    for i in internados:
        animal = db.query(Animal).filter(Animal.id == i.animal_id).first()
        if animal:
            pacientes_criticos.append({
                "internacao_id": i.id,
                "animal_id": animal.id,
                "nome_animal": animal.nome,
                "especie": animal.especie,
                "raca": animal.raca or "SRD",
                "leito": i.leito,
                "nivel_criticidade": getattr(i, 'nivel_criticidade', 'MÉDIO'),
                "motivo": i.motivo,
                "data_entrada": i.data_entrada.strftime("%d/%m/%Y %H:%M") if i.data_entrada else "N/I"
            })

    criticos_count = sum(1 for p in pacientes_criticos if p["nivel_criticidade"] in ["ALTO", "CRÍTICO", "EMERGÊNCIA"])

    consultas_agendadas = db.query(Consulta).filter(Consulta.status.in_(["AGENDADA", "EM_ATENDIMENTO", "Aguardando Triagem (Recepção)"])).all()
    
    triagem_prioridades = {
        "EMERGENCIA": 0,
        "URGENCIA": 0,
        "POUCO_URGENTE": 0,
        "NAO_URGENTE": 0
    }

    for c in consultas_agendadas:
        queixa = (c.queixa_principal or "").upper()
        if any(term in queixa for term in ["CHOQUE", "PARADA", "HEMORRAGIA", "CONVULSÃO", "INCONSCIENTE"]):
            triagem_prioridades["EMERGENCIA"] += 1
        elif any(term in queixa for term in ["VÔMITO", "FEBRE", "DOR", "DISPNEIA", "PROSTRAÇÃO"]):
            triagem_prioridades["URGENCIA"] += 1
        elif any(term in queixa for term in ["DIARREIA", "INAPETÊNCIA", "PRURIDO", "FERIDA"]):
            triagem_prioridades["POUCO_URGENTE"] += 1
        else:
            triagem_prioridades["NAO_URGENTE"] += 1

    historico_7_dias = []
    dias_semana_map = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
    for d in range(6, -1, -1):
        dia_alvo = hoje - timedelta(days=d)
        qtd = db.query(Consulta).filter(
            func.date(Consulta.data_consulta) == dia_alvo
        ).count()
        historico_7_dias.append({
            "data": dia_alvo.strftime("%d/%m"),
            "dia_semana": dias_semana_map[dia_alvo.weekday()],
            "atendimentos": qtd
        })

    return {
        "totais": {
            "total_pacientes_cadastrados": total_pacientes,
            "consultas_hoje": consultas_hoje,
            "total_agendamentos_hoje": total_agendamentos,
            "pacientes_internados": total_internados,
            "leitos_totais": leitos_totais,
            "leitos_disponiveis": leitos_disponiveis,
            "taxa_ocupacao_porcentagem": taxa_ocupacao,
            "casos_criticos": criticos_count
        },
        "lista_agendamentos_hoje": lista_agendamentos_hoje,
        "lista_internados_criticos": pacientes_criticos,
        "triagem_prioridades": triagem_prioridades,
        "historico_7_dias": historico_7_dias
    }


@router.post("/ia-resumo-dia")
def gerar_resumo_do_dia(
    dados: ResumoDiaRequest,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    hoje = date.today()
    total_internados = db.query(Internacao).filter(Internacao.status == "INTERNADO").count()
    total_agendamentos = db.query(Agendamento).filter(func.date(Agendamento.data_horario) == hoje).count()

    prompt_resumo = f"""
    Você é o assistente inteligente da clínica veterinária VetAssist AI.
    Gere uma síntese operacional objetiva para o utilizador com o perfil: {dados.perfil}.

    DADOS ATUAIS DA CLÍNICA HOJE ({hoje.strftime('%d/%m/%Y')}):
    - Agendamentos marcados para hoje: {total_agendamentos}
    - Leitos ocupados na Internação/UTI: {total_internados} de 10 leitos

    INSTRUÇÕES:
    - Mencione claramente o número de agendamentos previstos para hoje e o estado da internação.
    - Se o perfil for RECEPCAO: foque na receção dos tutores agendados e check-ins.
    - Escreva de 2 a 3 frases em Português, de forma profissional e direta.
    """

    client_g1, mod_g1 = obter_config_ia_dinamica("groq_1")
    if client_g1:
        try:
            res = client_g1.chat.completions.create(
                model=mod_g1,
                messages=[{"role": "user", "content": prompt_resumo}],
                max_tokens=220,
                temperature=0.3
            )
            return {"resumo": res.choices[0].message.content.strip()}
        except Exception as e:
            print(f"Aviso IA resumo: {e}")

    return {"resumo": f"Síntese Diária: Existem {total_agendamentos} agendamentos previstos para hoje e {total_internados} leitos ocupados na UTI. O fluxo operacional encontra-se estável."}


@router.post("/ia-assistente")
def assistente_ia_resposta(
    dados: MensagemIaRequest,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    hoje = date.today()
    total_internados = db.query(Internacao).filter(Internacao.status == "INTERNADO").count()
    total_agendamentos = db.query(Agendamento).filter(func.date(Agendamento.data_horario) == hoje).count()

    prompt = f"""
    Você é o Assistente Virtual Operacional do sistema VetAssist AI.
    Perfil do utilizador: {dados.perfil}.
    Contexto da clínica hoje: {total_agendamentos} agendamentos na agenda e {total_internados} leitos ocupados na UTI.

    Pergunta: "{dados.mensagem}"

    Responda em no máximo 3 frases concisas e diretas em Português.
    """

    client_g1, mod_g1 = obter_config_ia_dinamica("groq_1")
    if client_g1:
        try:
            res = client_g1.chat.completions.create(
                model=mod_g1,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=250,
                temperature=0.2
            )
            return {"resposta": res.choices[0].message.content.strip()}
        except Exception as e:
            print(f"Aviso IA assistente: {e}")

    return {"resposta": f"Temos {total_agendamentos} agendamentos marcados para hoje e {total_internados} pacientes internados na UTI. Como posso ajudar?"}