import os
import shutil
import time
import base64
from io import BytesIO
from pathlib import Path
from typing import List, Optional
from PIL import Image
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database.database import get_db
from models.consulta import Consulta
from models.animais import Animal
from models.usuario import Usuario  
from schemas.consulta import ConsultaUpdate
from services.security import obter_usuario_logado, exigir_perfil
from google import genai
from google.genai import types
from google.genai.errors import APIError
from groq import Groq
from openai import OpenAI
from dotenv import load_dotenv

router = APIRouter(
    prefix="/consultas",
    tags=["Consultas"]
)

UPLOADS_DIR = Path("uploads/exames")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv()

# Configuração de todas as chaves de IA
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client_groq = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

GEMINI_API_KEY_PRIMARY = os.getenv("GEMINI_API_KEY_PRIMARY")
GEMINI_API_KEY_SECONDARY = os.getenv("GEMINI_API_KEY_SECONDARY")
GEMINI_API_KEY_TERTIARY = os.getenv("GEMINI_API_KEY_TERTIARY")
GEMINI_API_KEY_QUATERNARY = os.getenv("GEMINI_API_KEY_QUATERNARY")

chaves_gemini = [
    GEMINI_API_KEY_PRIMARY, 
    GEMINI_API_KEY_SECONDARY, 
    GEMINI_API_KEY_TERTIARY, 
    GEMINI_API_KEY_QUATERNARY
]

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client_openai = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

class ConsultaCreate(BaseModel):
    codigo: Optional[str] = None
    animal_id: int
    usuario_id: Optional[int] = None
    status: Optional[str] = "AGUARDANDO_TRIAGEM"
    queixa_principal: Optional[str] = "Check-in de rotina / Recepção"
    historico_clinico: Optional[str] = None
    sintomas: Optional[str] = None
    exame_fisico: Optional[str] = None
    suspeita_diagnostica: Optional[str] = None
    peso_atendimento: Optional[float] = None
    temperatura: Optional[float] = None
    frequencia_cardiaca: Optional[int] = None
    frequencia_respiratoria: Optional[int] = None
    parecer_copiloto: Optional[str] = None
    observacoes: Optional[str] = None
    indicacao_cirurgia: Optional[bool] = False
    justificativa_cirurgica: Optional[str] = None
    solicitar_exames_preventivos: Optional[bool] = False

class CopilotoRequest(BaseModel):
    animal_id: Optional[int] = None
    especie: Optional[str] = "Não informada"
    raca: Optional[str] = "SRD"
    idade: Optional[str] = None
    peso: Optional[str] = None
    queixa_principal: str
    sintomas: Optional[str] = None
    exame_fisico: Optional[str] = None
    temperatura: Optional[str] = None
    frequencia_cardiaca: Optional[int] = None
    frequencia_respiratoria: Optional[int] = None

class SugestaoAsaRequest(BaseModel):
    queixa_principal: str
    historico_clinico: Optional[str] = None
    exame_fisico: Optional[str] = None
    temperatura: Optional[float] = None
    frequencia_cardiaca: Optional[int] = None
    frequencia_respiratoria: Optional[int] = None

@router.post("/upload-anexo")
async def upload_anexo_exame(
    file: UploadFile = File(...),
    usuario_logado = Depends(obter_usuario_logado)
):
    try:
        caminho_arquivo = UPLOADS_DIR / f"{file.filename}"
        with open(caminho_arquivo, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        return {
            "mensagem": "Arquivo enviado com sucesso!",
            "nome_arquivo": file.filename,
            "caminho": str(caminho_arquivo)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar arquivo: {str(e)}")

@router.post("/sugestoes-copiloto-multimodal")
async def gerar_sugestoes_copiloto_multimodal(
    queixa_principal: str = Form(...),
    animal_id: Optional[int] = Form(None),
    especie: Optional[str] = Form("Não informada"),
    raca: Optional[str] = Form("SRD"),
    idade: Optional[str] = Form(None),
    peso: Optional[str] = Form(None),
    sintomas: Optional[str] = Form(None),
    exame_fisico: Optional[str] = Form(None),
    temperatura: Optional[str] = Form(None),
    frequencia_cardiaca: Optional[int] = Form(None),
    frequencia_respiratoria: Optional[int] = Form(None),
    solicitar_exames_preventivos: Optional[str] = Form("false"),
    file: Optional[UploadFile] = File(None),
    usuario_logado = Depends(obter_usuario_logado),
    db: Session = Depends(get_db)
):
    if not queixa_principal or not queixa_principal.strip():
        raise HTTPException(status_code=400, detail="A queixa principal é obrigatória para consultar o copiloto.")

    if animal_id:
        animal = db.query(Animal).filter(Animal.id == animal_id).first()
        if animal:
            especie = animal.especie or especie
            raca = animal.raca or raca
            idade = f"{animal.idade} anos" if animal.idade else idade
            peso = f"{animal.peso} kg" if animal.peso else peso

    exames_desejados = str(solicitar_exames_preventivos).lower() == "true"
    diretriz_exames = (
        "O tutor SOLICITOU exames preventivos/check-up nesta visita. No plano diagnóstico, recomende explicitamente exames laboratoriais preventivos de rotina (como Hemograma Completo + Plaquetas e Ureia e Creatinina) para que o sistema possa listá-los."
        if exames_desejados else
        "O tutor NÃO solicitou exames preventivos (procedimento estritamente profilático/vacinação). Não recomende exames laboratoriais de rotina caso o paciente esteja hígido."
    )

    prompt_texto = f"""
    Você é o Copiloto Clínico de Inteligência Artificial do sistema VetAssist AI, especialista em Medicina Interna Veterinária, Diagnóstico por Imagem e Patologia Clínica.
    Sua função é auxiliar o médico veterinário durante a consulta, fornecendo triagem rápida, hipóteses diagnósticas e interpretação de exames/imagens.

    DADOS DO PACIENTE E ATENDIMENTO:
    - Espécie: {especie}
    - Raça: {raca}
    - Idade: {idade or 'Não informada'}
    - Peso: {peso or 'Não informado'}
    - Queixa Principal: {queixa_principal}
    - Sintomas / Histórico: {sintomas or 'Não informados'}
    - Exame Físico: {exame_fisico or 'Não informado'}
    - Sinais Vitais: Temp: {temperatura or '-'} °C | FC: {frequencia_cardiaca or '-'} bpm | FR: {frequencia_respiratoria or '-'} mpm
    - DIRETRIZ SOBRE EXAMES: {diretriz_exames}

    ORIENTAÇÕES SOBRE EXAMES E PRESCRIÇÕES:
    1. Forneça um plano diagnóstico abrangente.
    2. NÃO se limite a 2 ou 3 exames. Quando o quadro clínico ou a investigação exigir, recomende MAIS DE 5 EXAMES na lista (ex: Hemograma Completo + Plaquetas, Ureia e Creatinina, ALT e AST, Fosfatase Alcalina, Ultrassonografia Abdominal Total, Radiografia Torácica, Urinálise, Eletrolitograma, etc.).
    3. Na lista de prescrições, inclua os medicamentos necessários para suporte, sintomáticos, antibioticoterapia ou analgesia.

    ESTRUTURA DA RESPOSTA (seja direto, técnico, conciso e bem fundamentado):
    1. 🚨 **Triagem & Nível de Urgência**:
    2. 🖼️ / 🧪 **Análise do Exame Anexado (se fornecido)**:
    3. 🔍 **Hipóteses Diagnósticas Diferenciais**:
    4. 📋 **Plano Terapêutico & Próximos Passos Sugeridos**.

    AO FINAL DA SUA RESPOSTA, inclua OBRIGATORIAMENTE um bloco de código JSON isolado e válido contendo a indicação cirúrgica, a suspeita diagnóstica principal, exames sugeridos e prescrições.

    Siga estritamente esta estrutura JSON:
    ```json
    {{
      "indicacao_cirurgia": false,
      "justificativa_cirurgica": "Sem indicação cirúrgica.",
      "suspeita_diagnostica": "Descreva a principal suspeita aqui",
      "exames_sugeridos": [
        "Hemograma Completo + Plaquetas",
        "Ureia e Creatinina",
        "ALT (TGP) e AST (TGO)",
        "Fosfatase Alcalina (FA) e Gama GT",
        "Ultrassonografia Abdominal Total",
        "Urinálise Tipo I (EAS)",
        "Eletrolitograma (Na, K, Cl)"
      ],
      "prescricoes": [
        {{
          "medicamento": "Nome do Medicamento",
          "dosagem": "Dosagem",
          "frequencia": "Frequência",
          "duracao": "Duração",
          "observacoes": "Observações"
        }}
      ]
    }}
    ```
    """

    file_bytes = None
    mime_type = "image/jpeg"
    base64_image = None

    if file:
        file_bytes = await file.read()
        mime_type = file.content_type or "image/jpeg"
        if mime_type.startswith("image/"):
            try:
                img = Image.open(BytesIO(file_bytes))
                img.thumbnail((1024, 1024))
                output_buffer = BytesIO()
                img.save(output_buffer, format="JPEG", quality=80)
                file_bytes = output_buffer.getvalue()
                base64_image = base64.b64encode(file_bytes).decode("utf-8")
            except Exception:
                pass

    config_geracao = types.GenerateContentConfig(
        temperature=0.1,
        max_output_tokens=2500
    )

    sucesso = False
    texto_resp = ""
    provedor_usado = ""

    # 1. Tenta a Groq via SDK Oficial
    if client_groq:
        try:
            print("🚀 Acionando motor via GROQ (SDK Oficial - Qwen 3.8 27B)...")
            response_groq = client_groq.chat.completions.create(
                model="qwen/qwen3.8-27b",
                messages=[
                    {"role": "system", "content": "Você é o Copiloto Clínico de Inteligência Artificial do VetAssist AI."},
                    {"role": "user", "content": prompt_texto}
                ],
                max_tokens=2500,
                temperature=0.1
            )
            texto_resp = response_groq.choices[0].message.content
            if texto_resp:
                sucesso = True
                provedor_usado = "Groq (Qwen 3.8)"
        except Exception as e:
            print(f"⚠️ Erro Groq: {e}")

    # 2. Se a Groq falhar, testa sequencialmente as 4 chaves do Gemini
    if not sucesso:
        for idx, chave in enumerate(chaves_gemini):
            if not chave:
                continue
            try:
                print(f"🔄 Tentando Gemini (Chave {idx+1})...")
                temp_client = genai.Client(api_key=chave)
                contents = [prompt_texto]
                if file_bytes and not base64_image:
                    contents.append(types.Part.from_bytes(data=file_bytes, mime_type=mime_type))

                response = temp_client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=contents,
                    config=config_geracao,
                )
                if response and response.text:
                    texto_resp = response.text
                    sucesso = True
                    provedor_usado = f"Gemini (Chave {idx+1})"
                    break
            except Exception as e:
                print(f"⚠️ Gemini Chave {idx+1} falhou: {str(e)}")
                continue

    # 3. Se o Gemini falhar em todas as chaves, tenta a OpenAI
    if not sucesso and client_openai:
        try:
            print("🌐 Acionando fallback OpenAI...")
            response_openai = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt_texto}],
                max_tokens=2500,
                temperature=0.1
            )
            texto_resp = response_openai.choices[0].message.content
            if texto_resp:
                sucesso = True
                provedor_usado = "OpenAI"
        except Exception as e:
            print(f"⚠️ Erro OpenAI: {e}")

    # 4. Tratamento sem Contingência Mockada
    if not sucesso or not texto_resp:
        print("❌ Todos os provedores/chaves de IA falharam. Lançando HTTPException...")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="⚠️ Os serviços de IA estão temporariamente indisponíveis (limite de cota ou sobrecarga). Por favor, tente novamente em alguns instantes."
        )

    indicacao_ia = False
    justificativa_ia = ""
    suspeita_ia = ""
    exames_sugeridos_ia = []

    try:
        import json
        if "```json" in texto_resp:
            bloco = texto_resp.split("```json")[1].split("```")[0].strip()
            parsed = json.loads(bloco)
            if isinstance(parsed, dict):
                indicacao_ia = parsed.get("indicacao_cirurgia", False)
                justificativa_ia = parsed.get("justificativa_cirurgica", "")
                suspeita_ia = parsed.get("suspeita_diagnostica", "")
                exames_sugeridos_ia = parsed.get("exames_sugeridos", [])
    except Exception as e:
        print(f"Aviso ao realizar parse do JSON da IA: {e}")

    return {
        "sugestoes": texto_resp,
        "indicacao_cirurgia": indicacao_ia,
        "justificativa_cirurgica": justificativa_ia,
        "suspeita_diagnostica": suspeita_ia,
        "exames_sugeridos": exames_sugeridos_ia,
        "provedor": provedor_usado
    }

@router.post("/sugestoes-copiloto")
def gerar_sugestoes_copiloto(
    dados: CopilotoRequest,
    usuario_logado = Depends(obter_usuario_logado),
    db: Session = Depends(get_db)
):
    if not dados.queixa_principal or not dados.queixa_principal.strip():
        raise HTTPException(status_code=400, detail="A queixa principal é obrigatória para consultar o copiloto.")
    return {"sugestoes": "Utilize a análise multimodal na tela de atendimento."}

@router.post("/sugerir-asa")
def sugerir_classificacao_asa(
    dados: SugestaoAsaRequest,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    return {
        "classificacao_asa": "ASA I - Paciente Saudável",
        "justificativa": "Baseado nos parâmetros padrão.",
        "indicacao_cirurgia": False,
        "justificativa_cirurgica": "",
        "descricao_tecnica": "",
        "observacoes_pos": ""
    }

# =========================================================================
# ROTA PÚBLICA DO PAINEL (POSICIONADA ANTES DE /{consulta_id})
# =========================================================================
@router.get("/painel-chamadas")
def listar_chamadas_painel(
    db: Session = Depends(get_db)
):
    # Traz atendimentos ativos ordenando primeiro quem está Em Triagem / Em Atendimento
    consultas_ativas = db.query(Consulta).filter(
        Consulta.status.in_([
            "Aguardando Triagem (Recepção)",
            "Em Triagem",
            "Aguardando Consulta (Fila Vet)",
            "Em Atendimento",
            "Aguardando Vacina",
            "Em Vacinação"
        ])
    ).order_by(
        # Prioriza no topo quem está em chamada ativa
        Consulta.status.in_(["Em Triagem", "Em Atendimento"]).desc(),
        Consulta.id.desc()
    ).limit(10).all()

    resultado = []
    for c in consultas_ativas:
        animal = db.query(Animal).filter(Animal.id == c.animal_id).first()
        veterinario = db.query(Usuario).filter(Usuario.id == c.usuario_id).first() if c.usuario_id else None
        
        nome_tutor = "-"
        if animal:
            if hasattr(animal, 'tutor') and animal.tutor:
                nome_tutor = animal.tutor.nome
            elif hasattr(animal, 'tutor_nome') and animal.tutor_nome:
                nome_tutor = animal.tutor_nome

        # Define o local/sala baseado na etapa
        if c.status == "Em Triagem":
            sala_atribuida = "Sala de Triagem"
            etapa = "🩺 Triagem"
        elif c.status == "Em Atendimento":
            sala_atribuida = f"Consultório {(c.id % 3) + 1}"
            etapa = "👨‍⚕️ Consulta Médica"
        else:
            sala_atribuida = "Aguardar Recepção"
            etapa = "⏳ Espera"

        resultado.append({
            "id": c.id,
            "codigo": c.codigo or f"CNS-{c.id:04d}",
            "pet": animal.nome if animal else "Paciente",
            "tutor": nome_tutor,
            "veterinario": veterinario.nome if veterinario else "Equipe Veterinária",
            "status": c.status,
            "etapa": etapa,
            "sala": sala_atribuida
        })

    return resultado

# =========================================================================
# ROTA SINCRONIZADA DA FILA DE TRIAGEM
# =========================================================================
@router.get("/fila-triagem")
def listar_fila_triagem(
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    consultas_aguardando = db.query(Consulta).filter(
        Consulta.status.in_([
            "AGUARDANDO_TRIAGEM",
            "Aguardando Triagem (Recepção)",
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

# =========================================================================
# ROTAS GERAIS E DINÂMICAS DE CONSULTA
# =========================================================================
@router.get("/")
def listar_consultas(
    usuario_logado: str = Depends(obter_usuario_logado),
    db: Session = Depends(get_db)
):
    return db.query(Consulta).order_by(Consulta.id.desc()).all()

@router.post("/")
def criar_consulta(
    consulta: ConsultaCreate,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO", "RECEPCAO"])),
    db: Session = Depends(get_db)
):
    animal = db.query(Animal).filter(Animal.id == consulta.animal_id).first()
    if not animal:
        raise HTTPException(status_code=404, detail="Paciente (Animal) não encontrado.")

    id_vet = consulta.usuario_id
    if not id_vet:
        usuario_padrao = db.query(Usuario).filter(Usuario.perfil.in_(["VETERINARIO", "ADMIN"])).first()
        if usuario_padrao:
            id_vet = usuario_padrao.id
        else:
            raise HTTPException(status_code=404, detail="Nenhum usuário/veterinário cadastrado.")

    nova_consulta = Consulta(
        codigo=consulta.codigo,
        usuario_id=id_vet,
        animal_id=consulta.animal_id,
        status=getattr(consulta, 'status', 'AGUARDANDO_TRIAGEM'),
        queixa_principal=consulta.queixa_principal or "Check-in de rotina / Recepção",
        historico_clinico=consulta.historico_clinico,
        sintomas=consulta.sintomas,
        exame_fisico=consulta.exame_fisico,
        suspeita_diagnostica=getattr(consulta, 'suspeita_diagnostica', None),
        peso_atendimento=consulta.peso_atendimento,
        temperatura=consulta.temperatura,
        frequencia_cardiaca=consulta.frequencia_cardiaca,
        frequencia_respiratoria=consulta.frequencia_respiratoria,
        parecer_copiloto=consulta.parecer_copiloto,
        observacoes=consulta.observacoes,
        indicacao_cirurgia=getattr(consulta, 'indicacao_cirurgia', False),
        justificativa_cirurgica=getattr(consulta, 'justificativa_cirurgica', None)
    )

    try:
        db.add(nova_consulta)
        db.flush()
        if not nova_consulta.codigo:
            nova_consulta.codigo = f"CNS-{nova_consulta.id:04d}"
        db.commit()
        db.refresh(nova_consulta)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao cadastrar consulta: {str(e)}")

    return nova_consulta

@router.get("/{consulta_id}")
def buscar_consulta(consulta_id: int, db: Session = Depends(get_db)):
    consulta = db.query(Consulta).filter(Consulta.id == consulta_id).first()
    if not consulta:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    return consulta

@router.put("/{consulta_id}")
def atualizar_consulta(
    consulta_id: int,
    consulta: ConsultaUpdate,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO", "RECEPCAO"])),
    db: Session = Depends(get_db)
):
    consulta_db = db.query(Consulta).filter(Consulta.id == consulta_id).first()
    if not consulta_db:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")

    if consulta.animal_id:
        consulta_db.animal_id = consulta.animal_id
    if consulta.usuario_id:
        consulta_db.usuario_id = consulta.usuario_id
    if consulta.codigo:
        consulta_db.codigo = consulta.codigo
    if hasattr(consulta, 'status') and consulta.status:
        consulta_db.status = consulta.status

    consulta_db.queixa_principal = consulta.queixa_principal or consulta_db.queixa_principal
    consulta_db.historico_clinico = consulta.historico_clinico
    consulta_db.sintomas = consulta.sintomas
    consulta_db.exame_fisico = consulta.exame_fisico
    if hasattr(consulta, 'suspeita_diagnostica'):
        consulta_db.suspeita_diagnostica = consulta.suspeita_diagnostica
    consulta_db.peso_atendimento = consulta.peso_atendimento
    consulta_db.temperatura = consulta.temperatura
    consulta_db.frequencia_cardiaca = consulta.frequencia_cardiaca
    consulta_db.frequencia_respiratoria = consulta.frequencia_respiratoria
    consulta_db.parecer_copiloto = consulta.parecer_copiloto or consulta_db.parecer_copiloto
    consulta_db.observacoes = consulta.observacoes
    
    if hasattr(consulta, 'indicacao_cirurgia'):
        consulta_db.indicacao_cirurgia = consulta.indicacao_cirurgia
    if hasattr(consulta, 'justificativa_cirurgica'):
        consulta_db.justificativa_cirurgica = consulta.justificativa_cirurgica

    db.commit()
    db.refresh(consulta_db)
    return consulta_db

@router.delete("/{consulta_id}")
def excluir_consulta(
    consulta_id: int,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO", "RECEPCAO"])),
    db: Session = Depends(get_db)
):
    consulta = db.query(Consulta).filter(Consulta.id == consulta_id).first()
    if not consulta:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    db.delete(consulta)
    db.commit()
    return {"mensagem": "Consulta excluída com sucesso."}