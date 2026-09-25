import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database.database import get_db
from models.animais import Animal
from models.tutor import Tutor
from models.consulta import Consulta
from models.exame import Exame
from models.vacina import Vacina
from models.prescricao import Prescricao
from models.internacao import Internacao, EvolucaoInternacao
from models.prontuario import ProntuarioSalvo
from services.security import exigir_perfil
from pydantic import BaseModel

from google import genai
from openai import OpenAI
from dotenv import load_dotenv

router = APIRouter(
    prefix="/prontuarios",
    tags=["Prontuários & Copiloto IA"]
)

load_dotenv()

class ProntuarioSalvarRequest(BaseModel):
    animal_id: int
    resumo_ia: str = None

# Instancia os dois clientes
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


@router.post("/salvar")
def salvar_prontuario(
    dados: ProntuarioSalvarRequest,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    total_salvos = db.query(ProntuarioSalvo).count()
    proximo_num = total_salvos + 1
    codigo_gerado = f"PRO-{proximo_num:03d}"

    novo_registro = ProntuarioSalvo(
        codigo=codigo_gerado,
        animal_id=dados.animal_id,
        resumo_ia=dados.resumo_ia
    )
    
    db.add(novo_registro)
    db.commit()
    db.refresh(novo_registro)

    return {
        "mensagem": "Prontuário salvo com sucesso",
        "codigo": codigo_gerado,
        "id": novo_registro.id
    }


@router.get("/salvos/todos")
def listar_prontuarios_salvos(
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO", "TRIAGEM", "RECEPCAO"])),
    db: Session = Depends(get_db)
):
    prontuarios = db.query(ProntuarioSalvo).order_by(ProntuarioSalvo.id.desc()).all()
    resultado = []
    for p in prontuarios:
        animal = db.query(Animal).filter(Animal.id == p.animal_id).first()
        resultado.append({
            "id": p.id,
            "codigo": p.codigo,
            "animal_id": p.animal_id,
            "animal_nome": animal.nome if animal else "Desconhecido",
            "resumo_ia": p.resumo_ia,
            "data_criacao": p.data_criacao
        })
    return resultado


@router.delete("/salvos/{prontuario_id}")
def excluir_prontuario_salvo(
    prontuario_id: int,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    registro = db.query(ProntuarioSalvo).filter(ProntuarioSalvo.id == prontuario_id).first()
    if not registro:
        raise HTTPException(status_code=404, detail="Prontuário salvo não encontrado")
    
    db.delete(registro)
    db.commit()
    return {"mensagem": "Prontuário excluído com sucesso"}


@router.get("/{animal_id}")
def obter_prontuario(
    animal_id: int,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO", "TRIAGEM", "RECEPCAO"])),
    db: Session = Depends(get_db)
):
    animal = db.query(Animal).filter(Animal.id == animal_id).first()
    if not animal:
        raise HTTPException(status_code=404, detail="Animal não encontrado")

    tutor = db.query(Tutor).filter(Tutor.id == animal.tutor_id).first()
    vacinas = db.query(Vacina).filter(Vacina.animal_id == animal_id).all()
    consultas = db.query(Consulta).filter(Consulta.animal_id == animal_id).order_by(Consulta.data_consulta.desc()).all()

    internacoes = db.query(Internacao).filter(Internacao.animal_id == animal_id).order_by(Internacao.data_entrada.desc()).all()
    ids_internacao = [i.id for i in internacoes]
    evolucoes = db.query(EvolucaoInternacao).filter(EvolucaoInternacao.internacao_id.in_(ids_internacao)).order_by(EvolucaoInternacao.data_registro.desc()).all() if ids_internacao else []

    # Busca o resumo da IA mais recente salvo para este animal
    prontuario_salvo = db.query(ProntuarioSalvo).filter(ProntuarioSalvo.animal_id == animal_id).order_by(ProntuarioSalvo.id.desc()).first()
    resumo_salvo = prontuario_salvo.resumo_ia if prontuario_salvo else None

    historico_consultas = []
    for consulta in consultas:
        exames = db.query(Exame).filter(Exame.consulta_id == consulta.id).all()
        prescricoes = db.query(Prescricao).filter(Prescricao.consulta_id == consulta.id).all()

        historico_consultas.append({
            "consulta": consulta,
            "exames": exames,
            "prescricoes": prescricoes
        })

    return {
        "animal": animal,
        "tutor": tutor,
        "vacinas": vacinas,
        "consultas": historico_consultas,
        "internacoes": internacoes,
        "evolucoes_internacao": evolucoes,
        "resumo_ia_salvo": resumo_salvo
    }


@router.get("/{animal_id}/resumo")
def obter_resumo(
    animal_id: int,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO", "TRIAGEM", "RECEPCAO"])),
    db: Session = Depends(get_db)
):
    animal = db.query(Animal).filter(Animal.id == animal_id).first()
    if not animal:
        raise HTTPException(status_code=404, detail="Animal não encontrado")

    tutor = db.query(Tutor).filter(Tutor.id == animal.tutor_id).first()
    vacinas = db.query(Vacina).filter(Vacina.animal_id == animal_id).all()
    consultas = db.query(Consulta).filter(Consulta.animal_id == animal_id).all()
    internacoes = db.query(Internacao).filter(Internacao.animal_id == animal_id).all()
    ids_internacao = [i.id for i in internacoes]
    evolucoes = db.query(EvolucaoInternacao).filter(EvolucaoInternacao.internacao_id.in_(ids_internacao)).order_by(EvolucaoInternacao.data_registro.desc()).all() if ids_internacao else []

    dados_paciente = f"""
    PACIENTE: {animal.nome}
    Espécie: {animal.especie} | Raça: {animal.raca or 'SRD'} | Sexo: {getattr(animal, 'sexo', 'Macho')} | Peso: {getattr(animal, 'peso', 'N/I')} kg
    Tutor: {tutor.nome if tutor else 'Não cadastrado'}

    HISTÓRICO VACINAL:
    """
    if vacinas:
        for v in vacinas:
            dados_paciente += f"- Vacina: {v.nome_vacina} | Aplicação: {v.data_aplicacao}\n"
    else:
        dados_paciente += "- Nenhuma vacina registrada.\n"

    dados_paciente += "\nHISTÓRICO DE ATENDIMENTOS E CONSULTAS:\n"
    if consultas:
        for c in consultas:
            data_str = c.data_consulta.strftime('%d/%m/%Y') if c.data_consulta else "S/D"
            dados_paciente += f"\n• Data: {data_str} | Queixa Principal: {c.queixa_principal or 'N/I'}\n"
    else:
        dados_paciente += "- Nenhuma consulta registrada.\n"

    dados_paciente += "\nHISTÓRICO DE INTERNAÇÃO & SINAIS VITAIS RECENTES:\n"
    if internacoes:
        for i in internacoes:
            dados_paciente += f"- Leito: {i.leito} | Status: {i.status} | Criticidade: {i.nivel_criticidade} | Motivo: {i.motivo}\n"
        for ev in evolucoes[:5]:
            dados_paciente += f"  - Temp: {ev.temperatura}°C | FC: {ev.freq_cardiaca}bpm | FR: {ev.freq_respiratoria}mpm | Obs: {ev.observacoes}\n"
    else:
        dados_paciente += "- Sem histórico de internação.\n"

    prompt = f"""
    Você é o VetAssist AI, um Copiloto Especialista em Medicina Veterinária Clínico-Cirúrgica.
    Analise o prontuário do paciente e forneça um parecer clínico de inteligência estruturado em 3 seções:

    1. 🎯 **Hipóteses Diagnósticas Principais**
    2. ⚠️ **Alertas de Risco, Deterioração Vital ou Interações Medicamentosas**
    3. 📋 **Conduta Sugerida e Recomendações de Acompanhamento**

    DADOS DO PACIENTE:
    {dados_paciente}
    """

    resumo_final = None

    if gemini_client:
        for tentativa in range(2):
            try:
                response = gemini_client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=prompt,
                )
                resumo_final = response.text
                if resumo_final:
                    break
            except Exception as e_gemini:
                import time
                time.sleep(0.5)

    if not resumo_final and openai_client:
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Você é um assistente especialista em clínica médica veterinária."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3
            )
            resumo_final = response.choices[0].message.content
        except Exception as e_openai:
            pass

    if not resumo_final:
        resumo_final = (
            f"⚠️ **Serviços de IA temporariamente indisponíveis no momento.**\n\n"
            f"**Resumo Simplificado:** Paciente {animal.nome}, {animal.especie} ({animal.raca or 'SRD'}). "
            f"Possui {len(consultas)} consulta(s), {len(vacinas)} vacina(s) e {len(internacoes)} registro(s) de internação."
        )

    return {
        "animal_id": animal.id,
        "animal": animal.nome,
        "resumo": resumo_final
    }