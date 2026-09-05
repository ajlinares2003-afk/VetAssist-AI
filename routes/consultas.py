import os
import time
import shutil
from io import BytesIO
from pathlib import Path
from typing import Optional
from PIL import Image
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database.database import get_db
from models.consulta import Consulta
from models.animais import Animal
from models.usuario import Usuario  
from schemas.consulta import ConsultaCreate, ConsultaUpdate
from services.security import obter_usuario_logado, exigir_perfil
from google import genai
from google.genai import types

router = APIRouter(
    prefix="/consultas",
    tags=["Consultas"]
)

# Diretório para armazenamento dos exames e laudos anexados
UPLOADS_DIR = Path("uploads/exames")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Chave de API do projeto VetAssist AI - Dev 2
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY", "AQ.Ab8RN6LMpUCrpnzurot52-2VsxNS4alZS1xAGanNbmVnkkJqzw")
client = genai.Client(api_key=GEMINI_API_KEY)


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
    frequencia_cardiaca: Optional[str] = None
    frequencia_respiratoria: Optional[str] = None


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
    frequencia_cardiaca: Optional[str] = Form(None),
    frequencia_respiratoria: Optional[str] = Form(None),
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

    prompt_texto = f"""
    Você é o Copiloto Clínico de Inteligência Artificial do sistema VetAssist AI, especialista em Medicina Interna Veterinária, Diagnóstico por Imagem e Patologia Clínica.
    Sua função é auxiliar o médico veterinário durante a consulta, fornecendo triagem rápida, hipóteses diagnósticas e interpretação de exames.

    DADOS DO PACIENTE E ATENDIMENTO:
    - Espécie: {especie}
    - Raça: {raca}
    - Idade: {idade or 'Não informada'}
    - Peso: {peso or 'Não informado'}
    - Queixa Principal: {queixa_principal}
    - Sintomas / Histórico: {sintomas or 'Não informados'}
    - Exame Físico: {exame_fisico or 'Não informado'}
    - Sinais Vitais: Temp: {temperatura or '-'} °C | FC: {frequencia_cardiaca or '-'} bpm | FR: {frequencia_respiratoria or '-'} mpm

    INSTRUÇÕES DE ANÁLISE COMPLEMENTAR:
    Caso uma imagem ou documento (Raio-X, Ultrassom, Hemograma ou Laudo PDF) tenha sido anexado à requisição:
    1. Analise minuciosamente os achados visuais ou os parâmetros laboratoriais.
    2. Identifique alterações estruturais, radiográficas, ultrassonográficas ou marcadores fora do intervalo de referência.
    3. Relacione os achados do exame anexado diretamente com a queixa e os sintomas do paciente.

    ESTRUTURA DA RESPOSTA (seja direto, técnico, estruturado e bem fundamentado):
    1. 🚨 **Triagem & Nível de Urgência**:
    2. 🖼️ / 🧪 **Análise do Exame Anexado (se fornecido)**:
    3. 🔍 **Hipóteses Diagnósticas Diferenciais**:
    4. 📋 **Plano Terapêutico & Próximos Passos Sugeridos**:
    """

    contents = [prompt_texto]

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
                mime_type = "image/jpeg"
            except Exception as img_err:
                print(f"Aviso na otimização de imagem: {img_err}")

        part = types.Part.from_bytes(
            data=file_bytes,
            mime_type=mime_type
        )
        contents.append(part)

    # Nome exato do modelo oficial Gemini 3.6
    modelo_nome = "gemini-3.6-flash"

    try:
        response = client.models.generate_content(
            model=modelo_nome,
            contents=contents,
        )
        if response and response.text:
            print("✅ Análise gerada com sucesso!")
            return {"sugestoes": response.text}
    except Exception as e:
        print(f"❌ ERRO COMPLETO NO GEMINI: {str(e)}")

    return {
        "sugestoes": "⚠️ Não foi possível obter a análise no momento. Verifique os logs do servidor."
    }


@router.post("/sugestoes-copiloto")
def gerar_sugestoes_copiloto(
    dados: CopilotoRequest,
    usuario_logado = Depends(obter_usuario_logado),
    db: Session = Depends(get_db)
):
    if not dados.queixa_principal or not dados.queixa_principal.strip():
        raise HTTPException(status_code=400, detail="A queixa principal é obrigatória para consultar o copiloto.")

    especie = dados.especie
    raca = dados.raca
    idade = dados.idade
    peso = dados.peso

    if dados.animal_id:
        animal = db.query(Animal).filter(Animal.id == dados.animal_id).first()
        if animal:
            especie = animal.especie or especie
            raca = animal.raca or raca
            idade = f"{animal.idade} anos" if animal.idade else idade
            peso = f"{animal.peso} kg" if animal.peso else peso

    prompt = f"""
    Você é o Copiloto Clínico de Inteligência Artificial do sistema VetAssist AI, especialista em Medicina Interna Veterinária e Triagem Semiológica.
    Sua função é auxiliar o médico veterinário durante a consulta, fornecendo triagem rápida, hipóteses diagnósticas e condutas de suporte imediato.

    DADOS DO PACIENTE E ATENDIMENTO:
    - Espécie: {especie}
    - Raça: {raca}
    - Idade: {idade or 'Não informada'}
    - Peso: {peso or 'Não informado'}
    - Queixa Principal: {dados.queixa_principal}
    - Sintomas / Histórico: {dados.sintomas or 'Não informados'}
    - Exame Físico: {dados.exame_fisico or 'Não informado'}
    - Sinais Vitais: Temp: {dados.temperatura or '-'} °C | FC: {dados.frequencia_cardiaca or '-'} bpm | FR: {dados.frequencia_respiratoria or '-'} mpm

    DIRETRIZES DE RESPOSTA:
    1. 🚨 **Triagem & Nível de Urgência**:
    2. 🔍 **Hipóteses Diagnósticas Diferenciais**:
    3. 🧪 **Plano Diagnóstico Sugerido**:
    4. ⚠️ **Pontos de Atenção na Semiologia & Alertas**:

    Responda de forma direta, técnica, estruturada e objetiva.
    """

    modelo_nome = "gemini-3.6-flash"

    try:
        response = client.models.generate_content(
            model=modelo_nome,
            contents=prompt,
        )
        return {"sugestoes": response.text}
    except Exception as e:
        print(f"Erro em sugestoes-copiloto: {e}")

    return {
        "sugestoes": "⚠️ Não foi possível obter as sugestões do Copiloto no momento."
    }


@router.get("/")
def listar_consultas(
    usuario_logado: str = Depends(obter_usuario_logado),
    db: Session = Depends(get_db)
):
    consultas = db.query(Consulta).order_by(Consulta.id.desc()).all()
    return consultas


@router.post("/")
def criar_consulta(
    consulta: ConsultaCreate,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO", "RECEPCAO"])),
    db: Session = Depends(get_db)
):
    animal = db.query(Animal).filter(Animal.id == consulta.animal_id).first()
    if not animal:
        raise HTTPException(status_code=404, detail="Paciente (Animal) não encontrado.")

    usuario = db.query(Usuario).filter(Usuario.id == consulta.usuario_id).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuário/Veterinário não encontrado.")

    nova_consulta = Consulta(
        codigo=consulta.codigo,
        usuario_id=consulta.usuario_id,
        animal_id=consulta.animal_id,
        status=getattr(consulta, 'status', 'CONCLUIDA'),
        queixa_principal=consulta.queixa_principal,
        historico_clinico=consulta.historico_clinico,
        sintomas=consulta.sintomas,
        exame_fisico=consulta.exame_fisico,
        peso_atendimento=consulta.peso_atendimento,
        temperatura=consulta.temperatura,
        frequencia_cardiaca=consulta.frequencia_cardiaca,
        frequencia_respiratoria=consulta.frequencia_respiratoria,
        parecer_copiloto=consulta.parecer_copiloto,
        observacoes=consulta.observacoes
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
def buscar_consulta(
    consulta_id: int,
    db: Session = Depends(get_db)
):
    consulta = db.query(Consulta).filter(Consulta.id == consulta_id).first()
    if not consulta:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    return consulta


@router.put("/{consulta_id}")
def atualizar_consulta(
    consulta_id: int,
    consulta: ConsultaUpdate,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    consulta_db = db.query(Consulta).filter(Consulta.id == consulta_id).first()
    if not consulta_db:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")

    if consulta.animal_id:
        animal = db.query(Animal).filter(Animal.id == consulta.animal_id).first()
        if not animal:
            raise HTTPException(status_code=404, detail="Paciente (Animal) não encontrado.")
        consulta_db.animal_id = consulta.animal_id

    if consulta.usuario_id:
        usuario = db.query(Usuario).filter(Usuario.id == consulta.usuario_id).first()
        if not usuario:
            raise HTTPException(status_code=404, detail="Usuário/Veterinário não encontrado.")
        consulta_db.usuario_id = consulta.usuario_id

    if consulta.codigo:
        consulta_db.codigo = consulta.codigo
    if hasattr(consulta, 'status') and consulta.status:
        consulta_db.status = consulta.status

    consulta_db.queixa_principal = consulta.queixa_principal or consulta_db.queixa_principal
    consulta_db.historico_clinico = consulta.historico_clinico
    consulta_db.sintomas = consulta.sintomas
    consulta_db.exame_fisico = consulta.exame_fisico
    consulta_db.peso_atendimento = consulta.peso_atendimento
    consulta_db.temperatura = consulta.temperatura
    consulta_db.frequencia_cardiaca = consulta.frequencia_cardiaca
    consulta_db.frequencia_respiratoria = consulta.frequencia_respiratoria
    consulta_db.parecer_copiloto = consulta.parecer_copiloto or consulta_db.parecer_copiloto
    consulta_db.observacoes = consulta.observacoes

    db.commit()
    db.refresh(consulta_db)
    return consulta_db


@router.delete("/{consulta_id}")
def excluir_consulta(
    consulta_id: int,
    usuario_logado = Depends(exigir_perfil(["ADMIN", "VETERINARIO"])),
    db: Session = Depends(get_db)
):
    consulta = db.query(Consulta).filter(Consulta.id == consulta_id).first()
    if not consulta:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")

    db.delete(consulta)
    db.commit()
    return {"mensagem": "Consulta excluída com sucesso."}