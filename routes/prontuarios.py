import os
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database.database import get_db
from models.animais import Animal
from models.tutor import Tutor
from models.consulta import Consulta
from models.exame import Exame
from models.vacina import Vacina
from models.prescricao import Prescricao

from google import genai
from dotenv import load_dotenv

router = APIRouter(
    prefix="/prontuarios",
    tags=["Prontuários"]
)

# Inicializa o cliente da SDK Google GenAI
# Recomendado: Defina a variável de ambiente GOOGLE_API_KEY no seu sistema/terminal.
# Se preferir colar direto, substitua 'SUA_CHAVE_API_GEMINI_AQUI' pela sua chave da API.
load_dotenv()

GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)


@router.get("/{animal_id}")
def obter_prontuario(
    animal_id: int,
    db: Session = Depends(get_db)
):
    animal = (
        db.query(Animal)
        .filter(Animal.id == animal_id)
        .first()
    )
    if not animal:
        return {
            "erro": "Animal nao encontrado"
        }

    tutor = (
        db.query(Tutor)
        .filter(Tutor.id == animal.tutor_id)
        .first()
    )
    vacinas = (
        db.query(Vacina)
        .filter(Vacina.animal_id == animal_id)
        .all()
    )
    consultas = (
        db.query(Consulta)
        .filter(Consulta.animal_id == animal_id)
        .all()
    )

    historico_consultas = []
    for consulta in consultas:
        exames = (
            db.query(Exame)
            .filter(
                Exame.consulta_id == consulta.id
            )
            .all()
        )
        prescricoes = (
            db.query(Prescricao)
            .filter(
                Prescricao.consulta_id == consulta.id
            )
            .all()
        )

        historico_consultas.append(
            {
                "consulta": consulta,
                "exames": exames,
                "prescricoes": prescricoes
            }
        )

    return {
        "animal": animal,
        "tutor": tutor,
        "vacinas": vacinas,
        "consultas": historico_consultas
    }


@router.get("/{animal_id}/resumo")
def obter_resumo(
    animal_id: int,
    db: Session = Depends(get_db)
):
    animal = (
        db.query(Animal)
        .filter(Animal.id == animal_id)
        .first()
    )
    if not animal:
        return {
            "erro": "Animal nao encontrado"
        }

    tutor = (
        db.query(Tutor)
        .filter(Tutor.id == animal.tutor_id)
        .first()
    )
    vacinas = (
        db.query(Vacina)
        .filter(Vacina.animal_id == animal_id)
        .all()
    )
    consultas = (
        db.query(Consulta)
        .filter(Consulta.animal_id == animal_id)
        .all()
    )

    # 1. Estrutura os dados brutos do prontuário para enviar ao Gemini
    dados_paciente = f"""
    PACIENTE: {animal.nome}
    Espécie: {animal.especie} | Raça: {animal.raca or 'SRD'} | Idade: {animal.idade} anos | Peso: {animal.peso} kg | Sexo: {animal.sexo}
    Tutor: {tutor.nome if tutor else 'Não cadastrado'}

    HISTÓRICO VACINAL:
    """
    if vacinas:
        for v in vacinas:
            dados_paciente += f"- Vacina: {v.nome_vacina} | Aplicação: {v.data_aplicacao} | Reforço: {v.data_reforco}\n"
    else:
        dados_paciente += "- Nenhuma vacina registrada.\n"

    dados_paciente += "\nHISTÓRICO DE CONSULTAS E TRATAMENTOS:\n"
    if consultas:
        for c in consultas:
            data_str = c.data_consulta.strftime('%d/%m/%Y') if c.data_consulta else "S/D"
            dados_paciente += f"\n• Data: {data_str}\n"
            dados_paciente += f"  Queixa Principal: {c.queixa_principal}\n"

            exames = (
                db.query(Exame)
                .filter(Exame.consulta_id == c.id)
                .all()
            )
            if exames:
                for ex in exames:
                    dados_paciente += f"  - Exame: {ex.nome_exame} | Resultado: {ex.resultado}\n"

            prescricoes = (
                db.query(Prescricao)
                .filter(Prescricao.consulta_id == c.id)
                .all()
            )
            if prescricoes:
                for p in prescricoes:
                    dados_paciente += f"  - Prescrição: {p.medicamento} {p.dosagem or ''} ({p.frequencia or ''}) {p.duracao or ''}\n"
    else:
        dados_paciente += "- Nenhuma consulta registrada.\n"

    # 2. Constrói o Prompt para a IA
    prompt = f"""
    Você é um assistente médico veterinário especialista em clínica médica.
    Análise os dados do prontuário do paciente abaixo e elabore um **Resumo Clínico Inteligente** sucinto e direto focado no médico veterinário.

    Siga a estrutura:
    1. **Síntese Clínica**: Visão geral da condição do paciente.
    2. **Evolução & Tratamentos**: Resumo das condutas, medicamentos e exames.
    3. **Alertas & Observações**: Destaque vacinas pendentes, acompanhamentos necessários ou atenção especial.

    Seja objetivo (no máximo 3 parágrafos curtos).

    DADOS DO PACIENTE:
    {dados_paciente}
    """

    # 3. Chamada para a API do Gemini
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        resumo_final = response.text
    except Exception as error:
        print(f"Erro ao chamar a API do Gemini: {error}")
        # Fallback de segurança se a chave não estiver configurada ou a API indisponível
        resumo_final = (
            f"Paciente {animal.nome}, {animal.especie.lower()} da raça {animal.raca}, "
            f"{animal.idade} anos, {animal.peso} kg. "
            f"Possui {len(consultas)} atendimento(s) e {len(vacinas)} vacina(s) no histórico."
        )

    return {
        "animal_id": animal.id,
        "animal": animal.nome,
        "resumo": resumo_final
    }