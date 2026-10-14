import os
import shutil
import uuid
import logging
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from database.database import get_db, SessionLocal
from models.consulta import Consulta
from models.animais import Animal
from models.usuario import Usuario  
from schemas.consulta import ConsultaUpdate
from services.security import obter_usuario_logado, exigir_perfil
from services.texto_clinico import corrigir_siglas
from services.ia_service import gerar_texto, IAIndisponivelError
from services.barragem_sanitaria import (
    extrair_secoes, aplicar_barragem, detectar_doenca_oficial, exames_oficiais,
    doenca_por_sinais, parecer_da_doenca, doencas_da_especie, doencas_incompativeis,
)
from groq import Groq
from openai import OpenAI
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/consultas",
    tags=["Consultas"]
)

UPLOADS_DIR = Path("uploads/exames")

MIME_POR_EXTENSAO = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".png": "image/png", ".webp": "image/webp"}
# O Gemini aceita ~20 MB por requisição com arquivos embutidos: ficamos abaixo disso.
MAX_ANEXOS_IA = 4
# Prazo total por tentativa do Copiloto. Aumentável pelo ambiente sem alterar o código.
TIMEOUT_COPILOTO_SEGUNDOS = int(os.getenv("IA_TIMEOUT_COPILOTO", "90"))
# Anexos podem demorar mais para serem enviados e analisados pelo Gemini.
TIMEOUT_ANEXOS_SEGUNDOS = int(os.getenv("IA_TIMEOUT_ANEXOS", "120"))
MAX_BYTES_ANEXOS_IA = 14 * 1024 * 1024

# Termos que indicam cirurgia no parecer; só valem se NÃO vierem negados ("sem indicação de cirurgia").
_TERMOS_CIRURGIA = ("cirurgia", "cirúrgic", "hemilaminectomia", "corpectomia", "osteossíntese",
                    "laparotomia", "celiotomia")
_NEGACOES = ("sem ", "não ", "nao ", "evitar", "evite", "descart", "nenhuma", "ausência", "sem necessidade",
             "dispensa", "afasta")


def ha_indicacao_cirurgica(texto: str) -> bool:
    """True se o parecer indica cirurgia em pelo menos uma menção que não esteja negada."""
    t = (texto or "").lower()
    for termo in _TERMOS_CIRURGIA:
        inicio = 0
        while (pos := t.find(termo, inicio)) != -1:
            antes = t[max(0, pos - 35):pos]
            for sep in ".;:,\n":  # só vale a negação da MESMA oração
                antes = antes.rsplit(sep, 1)[-1]
            if not any(neg in antes for neg in _NEGACOES):
                return True
            inicio = pos + len(termo)
    return False

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Ajuste conforme os tipos de laudo/imagem que a clínica realmente anexa.
EXTENSOES_PERMITIDAS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
TAMANHO_MAX_BYTES = 15 * 1024 * 1024  # 15 MB


def salvar_anexo(file: UploadFile):
    """Valida extensão/tamanho e salva com nome UUID. Retorna (nome_salvo, caminho)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Arquivo sem nome.")

    ext = Path(file.filename).suffix.lower()
    if ext not in EXTENSOES_PERMITIDAS:
        permitidas = ", ".join(sorted(EXTENSOES_PERMITIDAS))
        raise HTTPException(status_code=400, detail=f"Tipo de arquivo não permitido. Use: {permitidas}.")

    novo_nome = f"{uuid.uuid4().hex}{ext}"
    caminho = UPLOADS_DIR / novo_nome
    total = 0
    try:
        with open(caminho, "wb") as buffer:
            while True:
                bloco = file.file.read(1024 * 1024)
                if not bloco:
                    break
                total += len(bloco)
                if total > TAMANHO_MAX_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Arquivo excede o limite de {TAMANHO_MAX_BYTES // (1024 * 1024)} MB.",
                    )
                buffer.write(bloco)
    except Exception:
        caminho.unlink(missing_ok=True)  # não deixa arquivo parcial em disco
        raise
    return novo_nome, caminho

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client_openai = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

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
            
            client = Groq(api_key=api_key, timeout=60.0) if api_key else None
            return "groq", client, modelo
        else:
            r_mod = db.execute(text("SELECT valor FROM configuracoes_sistema WHERE chave = 'gemini_model'")).fetchone()
            r_key = db.execute(text("SELECT valor FROM configuracoes_sistema WHERE chave = 'gemini_api_key'")).fetchone()
            
            modelo = r_mod[0] if r_mod and r_mod[0] else "gemini-3.8-flash"
            key_db = r_key[0] if r_key and r_key[0] and not str(r_key[0]).startswith("****") else None
            api_key = key_db or os.getenv("GEMINI_API_KEY_PRIMARY") or os.getenv("GEMINI_API_KEY")
            
            return "gemini", api_key, modelo
    finally:
        db.close()

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
    # Só são gravados se a coluna existir no model Consulta (ver criar_consulta).
    tpc_segundos: Optional[float] = None
    mucosas: Optional[str] = None
    ecc: Optional[str] = None

@router.post("/upload-anexo")
def upload_anexo_exame(
    file: UploadFile = File(...),
    usuario_logado = Depends(obter_usuario_logado)
):
    try:
        novo_nome, caminho_arquivo = salvar_anexo(file)
        return {
            "mensagem": "Arquivo enviado com sucesso!",
            "nome_arquivo": novo_nome,
            "nome_original": file.filename,
            "caminho": str(caminho_arquivo)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao salvar anexo: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Erro interno ao salvar o arquivo.")

@router.post("/sugestoes-copiloto-multimodal")
def sugestoes_copiloto_multimodal(
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
    tpc_segundos: Optional[str] = Form(None),
    mucosas: Optional[str] = Form(None),
    ecc: Optional[str] = Form(None),
    solicitar_exames_preventivos: Optional[str] = Form("false"),
    files: List[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    try:
        # Busca a espécie real do banco, sobrepondo o que vem do formulário
        animal_db = db.query(Animal).filter(Animal.id == animal_id).first() if animal_id else None
        especie_real = (animal_db.especie if animal_db and animal_db.especie else especie) or "Não informada"

        nomes_arquivos_originais = []
        anexos_salvos = []
        for file in files:
            if file.filename:
                novo_nome, _ = salvar_anexo(file)
                nomes_arquivos_originais.append(file.filename)
                anexos_salvos.append({"original": file.filename, "salvo": novo_nome})

        texto_anexos = ", ".join(nomes_arquivos_originais) if nomes_arquivos_originais else "Nenhum"

        # Lê do disco os arquivos que vão para a IA (limites de quantidade e de tamanho total).
        anexos_ia, nomes_ia, nomes_fora = [], [], []
        total_bytes = 0
        for anexo in anexos_salvos:
            caminho = UPLOADS_DIR / anexo["salvo"]
            try:
                dados = caminho.read_bytes()
            except OSError:
                nomes_fora.append(anexo["original"])
                continue
            if len(anexos_ia) >= MAX_ANEXOS_IA or total_bytes + len(dados) > MAX_BYTES_ANEXOS_IA:
                nomes_fora.append(anexo["original"])
                continue
            total_bytes += len(dados)
            anexos_ia.append((dados, MIME_POR_EXTENSAO.get(caminho.suffix.lower(), "application/octet-stream"),
                              anexo["original"]))
            nomes_ia.append(anexo["original"])

        # A conduta oficial (notificar, só testes oficiais, sem tratamento) é decidida em Python.
        # Aqui a IA só NOMEIA a doença; regras de conduta no prompt fazem a IA aplicá-las a doenças
        # que não são oficiais (e a citar doenças de outra espécie).
        lista_oficiais = doencas_da_especie(especie_real)
        if lista_oficiais:
            regra_sanitaria = (
                "DEFESA SANITÁRIA: se, e SOMENTE se, o quadro for compatível com doença de notificação "
                f"obrigatória que ocorre nesta espécie ({'; '.join(lista_oficiais)}), cite-a na SUSPEITA pelo nome "
                "em português. A conduta oficial é aplicada automaticamente pelo sistema: NÃO escreva "
                "eutanásia, notificação nem 'sem tratamento' para nenhuma outra doença, e NUNCA cite "
                "doença que não ocorre na espécie do paciente."
            )
        else:
            regra_sanitaria = ("DEFESA SANITÁRIA: não cite doenças de notificação oficial de outras espécies. "
                               "Não escreva eutanásia nem 'sem tratamento' como conduta padrão.")

        def _prompt_base(linha_anexos: str, regra_anexos: str) -> str:
            return (
            f"Atue como um médico veterinário especialista e copiloto clínico de excelência.\n"
            f"Analise o caso clínico abaixo e forneça obrigatoriamente uma suspeita diagnóstica principal objetiva e direta, considerando os diferenciais compatíveis com a espécie:\n"
            f"- Espécie: {especie_real} | Raça: {raca} | Idade: {idade} | Peso: {peso}\n"
            f"- Queixa Principal: {queixa_principal}\n"
            f"- Sintomas: {sintomas}\n"
            f"- Exame Físico: {exame_fisico}\n"
            f"{linha_anexos}"
            f"- Parâmetros Vitais: Temperatura={temperatura} °C | FC (frequência cardíaca)={frequencia_cardiaca} bpm | "
            f"FR (frequência respiratória)={frequencia_respiratoria} ir/min | "
            f"TPC (Tempo de Preenchimento Capilar)={tpc_segundos} s | Mucosas={mucosas} | "
            f"ECC (Escore de Condição Corporal, escala 1 a 9)={ecc or 'não informado'}\n\n"
            f"REGRAS OBRIGATÓRIAS:\n"
            f"1. Você DEVE fornecer uma suspeita diagnóstica principal exata, sem inventar nomes cruzados ou patologias de outras espécies.\n"
            f"2. PROIBIÇÃO ABSOLUTA DE TRATAMENTOS TÓXICOS POR ESPÉCIE: Gatos NÃO toleram e NÃO PODEM receber Paracetamol, Permetrina, Azul de Metileno ou AINEs em contexto de intoxicação.\n"
            f"2.1 TOXICOLOGIA DE ROEDORES/LAGOMORFOS: Proibido penicilinas, lincosamidas ou cefalosporinas via oral.\n"
            f"2.2 ANIMAIS DE PRODUÇÃO: DEVE incluir alerta explícito sobre o Período de Carência exigido para consumo humano de carne/leite.\n"
            f"2.3 USO RACIONAL DE ANTIMICROBIANOS: Nunca recomende profilaxia antibiótica para feridas superficiais limpas.\n"
            f"2.4 COMBATE AO 'OVERTESTING': Não sugira exames excessivos de urgência para pacientes estáveis.\n"
            f"2.5 {regra_sanitaria}\n"
            f"2.6 MANEJO VACINAL (MAPA): É proibido sugerir vacinação incorreta ou tratamentos empíricos.\n"
            f"2.7 PROIBIÇÃO DE FÁRMACOS HUMANOS EM GRANDES ANIMAIS: É TERMINANTEMENTE PROIBIDO prescrever medicamentos de uso humano (como Isoniazida, Rifampicina, Etambutol ou outros fármacos humanos restritos) para equinos, bovinos ou animais de grande porte.\n"
            f"3. PARTICULARIDADES: Respeite a fisiologia da espécie, sem converter doses para formatos humanos.\n"
            f"4. IDIOMA ESTREITO: Use vocabulário médico-veterinário brasileiro (ex: claudicação, metemoglobinemia). "
            f"A SUSPEITA deve estar SEMPRE em português do Brasil, sem termos nem siglas em inglês "
            f"(escreva o nome da doença por extenso em português). "
            f"Cite UMA única doença na SUSPEITA, sem parênteses com outra doença; os diferenciais vão em SUGESTOES.\n"
            f"5. Comente o estado do TPC e Mucosas.\n"
            f"6. TPC significa Tempo de Preenchimento Capilar.\n"
            f"7. Não sugira condutas de medicina humana.\n"
            f"8. Proporcionalidade na analgesia (ex: opioides só para dor moderada/grave).\n"
            f"9. Doses apenas em mg/kg.\n"
            f"10. RIGOR FARMACOLÓGICO E APRESENTAÇÕES COMERCIAIS: NUNCA invente efeitos ou prescreva remédios humanos para grandes animais.\n"
            f"11. ESTRUTURA LÓGICA DE RACIOCÍNIO (CHAIN OF THOUGHT): Antes de sugerir a conduta, avalie obrigatoriamente os sinais vitais. Classifique como 'ESTÁVEL' ou 'CRÍTICO'.\n"
            f"{regra_anexos}"
            f"12. Responda estritamente no formato abaixo, sem adicionar introduções:\n"
            f"ESTADO DO PACIENTE: [Declarar se está ESTÁVEL ou CRÍTICO baseado apenas nos sinais vitais fornecidos]\n"
            f"SUSPEITA: [Nome exato e direto, em português do Brasil e sem termos em inglês, de UMA única hipótese principal]\n"
            f"SUGESTOES: [Análise clínica, conduta e exames pertinentes ao caso]"
            )

        def montar_prompt(com_anexos: bool) -> str:
            if com_anexos:
                linha_anexos = (f"- Exames/Laudos Anexados (imagens/PDFs enviados junto com este texto, na mesma ordem): "
                                f"{', '.join(nomes_ia)}\n")
                regra_anexos = (
                    "11.1 ANEXOS: analise o que é VISÍVEL em cada arquivo como SEGUNDA LEITURA de apoio, nunca como laudo. "
                    "Identifique a região anatômica e o tipo de exame SOMENTE pelo conteúdo da imagem, jamais pelo NOME do "
                    "arquivo (o nome pode ser o do paciente). Se a qualidade, o posicionamento ou o recorte não permitirem "
                    "concluir, escreva 'não é possível concluir'. Não invente achados. Em SUGESTOES inclua um parágrafo "
                    "iniciado por 'Achados nos anexos (segunda leitura, não substitui laudo):' e aponte se a imagem concorda "
                    "ou diverge do quadro clínico informado.\n")
            else:
                # Sem o conteúdo, o NOME do arquivo também é omitido: modelos de texto deduzem o exame
                # pelo nome ("Raio X Thor.jpg" virou "radiografia torácica").
                n = len(nomes_arquivos_originais)
                linha_anexos = (f"- Exames/Laudos Anexados: {n} arquivo(s) registrado(s); nomes e conteúdo NÃO foram "
                                f"enviados a você\n" if n else "- Exames/Laudos Anexados: Nenhum\n")
                regra_anexos = (
                    "11.1 ANEXOS: você NÃO viu o conteúdo dos arquivos anexados. NÃO mencione, descreva, suponha nem solicite "
                    "nenhum exame com base nos anexos (nem tipo de exame, nem região anatômica); apenas diga, em uma frase, "
                    "que o veterinário deve avaliar os arquivos anexados.\n")
            return _prompt_base(linha_anexos, regra_anexos)

        prompt_sem_anexos = montar_prompt(False)
        prompt_ia = montar_prompt(True) if anexos_ia else prompt_sem_anexos

        # Mesmo caminho das outras telas: Gemini -> Groq 1 -> Groq 2, com fallback automático.
        # Com anexos, só o Gemini os recebe; se ele falhar, a Groq responde só com o texto clínico.
        try:
            resposta_ia = gerar_texto(
                db, prompt_ia,
                timeout=TIMEOUT_ANEXOS_SEGUNDOS if anexos_ia else TIMEOUT_COPILOTO_SEGUNDOS,
                anexos=anexos_ia or None, prompt_sem_anexos=prompt_sem_anexos,
            )
        except IAIndisponivelError as exc:
            logger.error("Copiloto: nenhuma IA respondeu. %s", " || ".join(exc.tentativas) or exc)
            if not exc.tentativas:  # nada configurado
                raise HTTPException(status_code=503, detail=str(exc))
            raise HTTPException(
                status_code=503,
                detail="Nenhuma IA respondeu agora. Tente de novo em instantes ou confira Configurações de IA.",
            )
        texto_resposta = resposta_ia.texto
        logger.info("Copiloto respondido por %s (%s); anexos analisados: %s",
                    resposta_ia.provedor, resposta_ia.modelo, resposta_ia.anexos_analisados)

        # Utiliza o parser robusto do novo módulo
        suspeita_extraida, sugestoes_extraidas = extrair_secoes(texto_resposta)
        suspeita_bruta = suspeita_extraida  # guarda antes do fallback "Hipótese clínica a esclarecer"

        if not suspeita_extraida:
            suspeita_extraida = "Hipótese clínica a esclarecer"

        suspeita_extraida = corrigir_siglas(suspeita_extraida)
        sugestoes_extraidas = corrigir_siglas(sugestoes_extraidas)
        
        # Aplica a guilhotina higienizada do módulo barragem_sanitaria
        # Se o parsing não achou a suspeita, avalia o texto bruto (falha para o lado seguro)
        suspeita_para_barragem = suspeita_bruta or texto_resposta
        doenca_oficial = detectar_doenca_oficial(suspeita_para_barragem, especie_real)
        parecer_oficial = aplicar_barragem(suspeita_para_barragem, especie_real, texto_resposta)
        if parecer_oficial:
            sugestoes_extraidas = parecer_oficial
        else:
            # A IA não nomeou doença oficial: confere pelos SINAIS (ex.: antraz descrito como "septicemia")
            texto_clinico = " ".join(str(x or "") for x in (queixa_principal, sintomas, exame_fisico))
            doenca_oficial = doenca_por_sinais(texto_clinico, especie_real)
            if doenca_oficial:
                logger.warning("barragem por sinais: %s (IA sugeriu %r)", doenca_oficial["nome"], suspeita_extraida)
                sugestoes_extraidas = parecer_da_doenca(doenca_oficial, por_sinais=True)
                suspeita_extraida = (f"{doenca_oficial['nome']} - suspeita pelos sinais clínicos "
                                     f"(a IA sugeriu: {suspeita_extraida})")[:300]
            else:
                # Sem barragem: se a IA citou doença exclusiva de OUTRA espécie, avisa em vez de deixar passar
                erradas = doencas_incompativeis(texto_resposta, especie_real)
                if erradas:
                    sugestoes_extraidas += ("\n\n⚠️ **Revisar:** o parecer cita " + "; ".join(erradas) +
                                            f", incompatível com a espécie do paciente ({especie_real}). "
                                            "Desconsidere essa menção e a conduta ligada a ela.")

        parecer_com_anexos = sugestoes_extraidas
        if nomes_arquivos_originais:
            if resposta_ia.anexos_analisados:
                nota = (f"analisados pela IA como segunda leitura ({', '.join(nomes_ia)}); NÃO substitui o laudo — "
                        "confirme com o veterinário/radiologista")
                if nomes_fora:
                    nota += f". Não enviados à IA por limite de tamanho ou quantidade: {', '.join(nomes_fora)}"
            else:
                nota = "arquivos registrados; o conteúdo NÃO foi analisado pela IA"
            parecer_com_anexos += f"\n\n📎 **Exames/Laudos Anexados:** {texto_anexos} ({nota})"

        tem_indicacao = (not doenca_oficial) and ha_indicacao_cirurgica(sugestoes_extraidas)

        # Texto neutro: não presume o tipo de cirurgia (a justificativa fixa anterior falava de coluna).
        justificativa_cirurgica_texto = None
        if tem_indicacao:
            justificativa_cirurgica_texto = ("O parecer menciona possível indicação cirúrgica. Confirme com exame clínico e "
                                             "de imagem antes de decidir e agendar.")

        return {
            "sugestoes": parecer_com_anexos,
            "suspeita_diagnostica": suspeita_extraida,
            "exames_anexados": texto_anexos if nomes_arquivos_originais else None,
            "anexos": anexos_salvos,
            "indicacao_cirurgia": tem_indicacao,
            "justificativa_cirurgica": justificativa_cirurgica_texto,
            "doenca_oficial": doenca_oficial["nome"] if doenca_oficial else None,
            "exames_oficiais": exames_oficiais(doenca_oficial) if doenca_oficial else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao processar IA: {e}", exc_info=True)
        # Oculta os detalhes internos de vazarem pro frontend
        raise HTTPException(status_code=500, detail="Erro interno ao processar a análise com o copiloto.")

@router.put("/{consulta_id}/chamar")
def chamar_paciente_consulta(
    consulta_id: int,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    consulta_db = db.query(Consulta).filter(Consulta.id == consulta_id).first()
    if not consulta_db:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    
    consulta_db.status = "Chamando para Consulta"
    db.commit()
    db.refresh(consulta_db)
    return {"mensagem": "Paciente chamado para consulta com sucesso!", "status": consulta_db.status}

@router.put("/{consulta_id}/chamar-triagem")
def chamar_paciente_triagem(
    consulta_id: int,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    consulta_db = db.query(Consulta).filter(Consulta.id == consulta_id).first()
    if not consulta_db:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    
    consulta_db.status = "Chamando para Triagem"
    db.commit()
    db.refresh(consulta_db)
    return {"mensagem": "Paciente chamado para triagem com sucesso!", "status": consulta_db.status}

@router.put("/{consulta_id}/iniciar-triagem")
def iniciar_triagem_consulta(
    consulta_id: int,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    consulta_db = db.query(Consulta).filter(Consulta.id == consulta_id).first()
    if not consulta_db:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    
    consulta_db.status = "Em Triagem"
    db.commit()
    db.refresh(consulta_db)
    return {"mensagem": "Triagem iniciada com sucesso!", "status": consulta_db.status}

@router.get("/painel-chamadas")
def listar_chamadas_painel(db: Session = Depends(get_db)):
    consultas_ativas = db.query(Consulta).filter(
        Consulta.status.in_([
            "AGUARDANDO_TRIAGEM",
            "Aguardando Triagem (Recepção)",
            "Aguardando Triagem",
            "Chamando para Triagem",
            "Em Triagem",
            "Aguardando Consulta (Fila Vet)",
            "Chamando para Consulta",
            "Em Atendimento",
            "Aguardando Vacina",
            "Em Vacinação"
        ])
    ).order_by(
        Consulta.status.in_(["Chamando para Triagem", "Chamando para Consulta", "Em Triagem", "Em Atendimento"]).desc(),
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

        if c.status == "Chamando para Triagem":
            sala_atribuida = "Sala de Triagem"
            etapa = "📢 Chamando para Triagem"
        elif c.status == "Chamando para Consulta":
            sala_atribuida = f"Consultório {(c.id % 3) + 1}"
            etapa = "📢 Chamando para Consulta"
        elif c.status == "Em Triagem":
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
            "Em Triagem",
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

@router.get("/")
def listar_consultas(
    usuario_logado: str = Depends(obter_usuario_logado),
    db: Session = Depends(get_db)
):
    consultas = db.query(Consulta).order_by(Consulta.id.desc()).all()
    
    for c in consultas:
        if c.peso_atendimento is None or c.temperatura is None:
            try:
                triagem = db.execute(
                    text("SELECT peso, temperatura FROM triagem WHERE consulta_id = :cid ORDER BY id DESC LIMIT 1"),
                    {"cid": c.id}
                ).fetchone()
                if triagem:
                    c.peso_atendimento = c.peso_atendimento or triagem[0]
                    c.temperatura = c.temperatura or triagem[1]
            except Exception:
                db.rollback()
                try:
                    triagem = db.execute(
                        text("SELECT peso, temperatura FROM triagens WHERE consulta_id = :cid ORDER BY id DESC LIMIT 1"),
                        {"cid": c.id}
                    ).fetchone()
                    if triagem:
                        c.peso_atendimento = c.peso_atendimento or triagem[0]
                        c.temperatura = c.temperatura or triagem[1]
                except Exception:
                    db.rollback()
                    
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

    # Campos extras só entram se o model tiver a coluna (evita erro em bancos ainda sem a migração).
    for campo in ("tpc_segundos", "mucosas", "ecc"):
        valor = getattr(consulta, campo, None)
        if valor is not None and hasattr(Consulta, campo):
            setattr(nova_consulta, campo, valor)

    try:
        db.add(nova_consulta)
        db.flush()
        if not nova_consulta.codigo:
            nova_consulta.codigo = f"CNS-{nova_consulta.id:04d}"
        db.commit()
        db.refresh(nova_consulta)
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao cadastrar consulta: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Erro interno ao cadastrar a consulta.")

    return nova_consulta

@router.get("/{consulta_id}")
def buscar_consulta(
    consulta_id: int,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)  # prontuário: exige login
):
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

    dados = consulta.model_dump(exclude_unset=True)
    nao_aceitam_vazio = {"animal_id", "usuario_id", "codigo", "status", "queixa_principal", "parecer_copiloto"}

    for campo, valor in dados.items():
        if campo in nao_aceitam_vazio and not valor:
            continue
        if hasattr(Consulta, campo):
            setattr(consulta_db, campo, valor)

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
