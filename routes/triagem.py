import logging
import re
import unicodedata
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database.database import get_db
from models.consulta import Consulta
from models.animais import Animal
from models.usuario import Usuario  # noqa: F401
from models.triagem import Triagem

# === CORREÇÃO: Importação segura ===
try:
    from models.biblioteca_referencia import BibliotecaReferencia
    _BIBLIOTECA_DISPONIVEL = True
except ImportError:
    BibliotecaReferencia = None
    _BIBLIOTECA_DISPONIVEL = False
    logging.getLogger(__name__).warning(
        "⚠️ Classe BibliotecaReferencia não encontrada — gravação de referências desativada temporariamente"
    )
# ===================================

from services.biblioteca_referencia import buscar_referencia_oficial
from services.referencia_auto import metadados_da_linha
from services.ia_service import IAIndisponivelError, gerar_json
from services.security import obter_usuario_logado

logger = logging.getLogger("vetassist.triagem")

router = APIRouter(
    prefix="/triagem",
    tags=["Triagem"]
)

# --------------------------------------------------------------------------- #
# Constantes clínicas
# --------------------------------------------------------------------------- #
CORES_MANCHESTER = ("VERMELHO", "LARANJA", "AMARELO", "VERDE", "AZUL")
ECC_REF_PADRAO = "💡 Ideal: 3/9 (Escala 1 a 9)"
LIMITE_TEMP_ALTA = 40.5
LIMITE_TEMP_BAIXA = 35.0
LIMITE_TPC_SEG = 3
TERMOS_EMERGENCIA = ("inconsciente", "inconsciencia", "desmai", "decubito")

RACAS_GENERICAS = {
    "srd",
    "sem raça definida",
    "sem raca definida",
    "sem raça",
    "sem raca",
    "",
    None,
}

REFERENCIA_INDISPONIVEL = {
    "peso_ref": "⚠️ Ref. Peso: indisponível",
    "ecc_ref": ECC_REF_PADRAO,
    "temperatura": "Referência indisponível. Consulte o veterinário",
    "fc": "Referência indisponível. Consulte o veterinário",
    "fr": "Referência indisponível. Consulte o veterinário",
    "tpc": "Referência indisponível. Consulte o veterinário",
    "mucosas": "⚠️ Referência indisponível",
    "fonte_ref": "⚠️ Sem referência oficial e a pesquisa automática não encontrou literatura confiável. Consulte o veterinário.",
    "indisponivel": True,
}

# --------------------------------------------------------------------------- #
# Modelos
# --------------------------------------------------------------------------- #
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
    _referencia_para_gravar: Optional[dict] = None

class AvaliacaoIARequest(BaseModel):
    animal_id: Optional[int] = None
    especie: Optional[str] = "Felino"
    sub_especie: Optional[str] = None
    raca: Optional[str] = None
    porte: Optional[str] = None
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

# --------------------------------------------------------------------------- #
# Auxiliares
# --------------------------------------------------------------------------- #
def _normalizar(texto: str) -> str:
    if not texto:
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in sem_acento if not unicodedata.combining(c))

def _normalizar_raca(raca: Optional[str]) -> str:
    if not raca:
        return ""
    return _normalizar(raca.strip())

def _eh_raca_generica(raca: Optional[str]) -> bool:
    return _normalizar_raca(raca) in RACAS_GENERICAS

def _extrair_faixa(texto: str) -> tuple:
    if not texto:
        return (None, None)
    nums = [float(n) for n in re.findall(r"\d+\.?\d*", texto)]
    return (nums[0], nums[1]) if len(nums) >= 2 else (None, None)

def _buscar_referencias_na_ia(especie: str, porte: str = None, sexo: str = None) -> Optional[dict]:
    """Busca valores na IA para exibição imediata — grava ao finalizar triagem."""
    prompt = f"""Você é um veterinário fisiologista. Forneça os valores de referência vitais 
para {especie} {f'porte {porte}' if porte else ''}, com base em literatura científica confiável.
Retorne APENAS um JSON com estas chaves:
{{
  "peso_ref": "X-Y kg",
  "ecc_ideal": "3/9",
  "temp_repouso": "min-max °C",
  "temp_clinica": "min-max °C",
  "fc_repouso": "min-max bpm",
  "fc_clinica": "min-max bpm",
  "fr_repouso": "min-max ir/min",
  "fr_clinica": "min-max ir/min",
  "tpc_ref": "≤ X segundos",
  "mucosas_ref": "descrição padrão"
}}
Não invente valores. Se não souber com segurança, responda apenas: "indisponível"."""

    try:
        resposta = gerar_json(None, prompt, validar=lambda d: d)
        r = resposta.dados
        if not r or "indisponível" in str(r).lower():
            return None

        t_rep_min, t_rep_max = _extrair_faixa(r.get("temp_repouso", ""))
        t_cli_min, t_cli_max = _extrair_faixa(r.get("temp_clinica", ""))
        fc_rep_min, fc_rep_max = _extrair_faixa(r.get("fc_repouso", ""))
        fc_cli_min, fc_cli_max = _extrair_faixa(r.get("fc_clinica", ""))
        fr_rep_min, fr_rep_max = _extrair_faixa(r.get("fr_repouso", ""))
        fr_cli_min, fr_cli_max = _extrair_faixa(r.get("fr_clinica", ""))
        p_min, p_max = _extrair_faixa(r.get("peso_ref", ""))

        if not all([t_rep_min, fc_rep_min, fr_rep_min]):
            return None

        dados_gravar = {
            "especie": especie,
            "raca": None,
            "porte": porte,
            "sexo": sexo,
            "peso_min": p_min,
            "peso_max": p_max,
            "ecc_ideal": r.get("ecc_ideal", "3/9"),
            "temp_repouso_min": t_rep_min,
            "temp_repouso_max": t_rep_max,
            "temp_clinica_min": t_cli_min,
            "temp_clinica_max": t_cli_max,
            "fc_repouso_min": fc_rep_min,
            "fc_repouso_max": fc_rep_max,
            "fc_clinica_min": fc_cli_min,
            "fc_clinica_max": fc_cli_max,
            "fr_repouso_min": fr_rep_min,
            "fr_repouso_max": fr_rep_max,
            "fr_clinica_min": fr_cli_min,
            "fr_clinica_max": fr_cli_max,
            "tpc_ref": r.get("tpc_ref", "≤ 2 segundos"),
            "mucosas_ref": r.get("mucosas_ref", "Rosadas, úmidas e brilhantes"),
            "fonte_bibliografica": "Pesquisa automática via IA",
            "status": "PENDENTE"
        }

        return {
            "peso_ref": f"💡 Ref. Peso: {p_min or '—'} - {p_max or '—'} kg",
            "ecc_ref": f"💡 Ideal: {r.get('ecc_ideal', '3/9')}",
            "temperatura": f"Normal: Repouso {t_rep_min}-{t_rep_max}°C | Clínica {t_cli_min}-{t_cli_max}°C",
            "fc": f"Repouso {fc_rep_min}-{fc_rep_max} bpm | Clínica {fc_cli_min}-{fc_cli_max} bpm",
            "fr": f"Repouso {fr_rep_min}-{fr_rep_max} ir/min | Clínica {fr_cli_min}-{fr_cli_max} ir/min",
            "tpc": f"{r.get('tpc_ref', '≤ 2 segundos')}",
            "mucosas": f"💡 {r.get('mucosas_ref', 'Rosadas e úmidas')}",
            "_dados_para_gravar": dados_gravar,
        }
    except Exception as e:
        logger.warning(f"Falha na busca de referências: {e}")
        return None

def _buscar_com_fallback(db: Session, perfil: dict) -> tuple:
    motivo = None
    bib = buscar_referencia_oficial(db, **perfil)
    if not bib and _eh_raca_generica(perfil.get("raca")):
        perfil_fallback = {
            "especie": perfil.get("especie"),
            "porte": perfil.get("porte"),
            "sexo": perfil.get("sexo"),
        }
        bib = buscar_referencia_oficial(db, **perfil_fallback)
        if bib:
            motivo = f"Perfil genérico — referência por porte: {perfil.get('porte')}"
    return bib, motivo

def _validar_avaliacao(dados: dict) -> dict:
    cor = str(dados.get("classificacao_risco", "")).strip().upper()
    justificativa = dados.get("justificativa")
    if cor not in CORES_MANCHESTER:
        raise ValueError(f"Classificação inválida: {cor!r}")
    if not isinstance(justificativa, str) or not justificativa.strip():
        raise ValueError("Justificativa ausente.")
    return {"classificacao_risco": cor, "justificativa": justificativa.strip()}

def _valor_ou_nao_aferido(valor, unidade: str) -> str:
    return f"{valor} {unidade}" if valor is not None else "não aferido"

def _contexto_referencia(bib, pendente: bool = False) -> str:
    if not bib:
        return (
            "NÃO HÁ faixas de referência para esta espécie/raça. Portanto NÃO julgue se os sinais "
            "vitais estão normais ou alterados e NÃO os compare com os de gato ou cão doméstico. "
            "Classifique APENAS pela queixa principal e diga na justificativa que os sinais vitais "
            "não puderam ser avaliados por falta de referência. A falta de referência, por si só, "
            "NÃO é motivo para elevar a urgência.\n"
        )
    aviso = (
        "ATENÇÃO: estas faixas foram geradas automaticamente por IA a partir de pesquisa na web e "
        "AINDA NÃO foram validadas por veterinário. Use com cautela e mencione isso na justificativa.\n"
        if pendente else ""
    )
    return (
        aviso +
        "Faixas de referência oficiais curadas para esta espécie/raça:\n"
        f"- Temperatura: repouso {bib.temp_repouso_min}-{bib.temp_repouso_max} °C | "
        f"clínica {bib.temp_clinica_min}-{bib.temp_clinica_max} °C\n"
        f"- FC: repouso {bib.fc_repouso_min}-{bib.fc_repouso_max} bpm | "
        f"clínica {bib.fc_clinica_min}-{bib.fc_clinica_max} bpm\n"
        f"- FR: repouso {bib.fr_repouso_min}-{bib.fr_repouso_max} ir/min | "
        f"clínica {bib.fr_clinica_min}-{bib.fr_clinica_max} ir/min\n"
        f"- TPC: {bib.tpc_ref}\n"
        f"- Mucosas: {bib.mucosas_ref}\n"
    )

# --------------------------------------------------------------------------- #
# Referências fisiológicas
# --------------------------------------------------------------------------- #
@router.post("/referencias-ia")
def calcular_referencias_ia(
    dados: ReferenciasIARequest,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    especie = (dados.especie or "").strip().capitalize()
    sub_especie = (dados.sub_especie or "").strip() or None
    raca = _normalizar_raca(dados.raca)
    porte = (dados.porte or "").strip() or None
    sexo = (dados.sexo or "").strip() or None

    perfil = dict(
        especie=especie,
        sub_especie=sub_especie,
        raca=raca,
        porte=porte,
        sexo=sexo,
    )

    bib = None
    motivo = None

    # ─── PASSO 1: Busca exata ───
    bib = buscar_referencia_oficial(db, **perfil)
    if bib:
        motivo = "Perfil encontrado no banco de dados"

    # ─── PASSO 2: SRD/genérica → tenta por porte ───
    if not bib and _eh_raca_generica(raca):
        perfil_porte = dict(perfil)
        perfil_porte["raca"] = None
        bib = buscar_referencia_oficial(db, **perfil_porte)
        if bib:
            motivo = f"Raça genérica → usando referência por porte: {porte}"

    # ─── PASSO 3: Não encontrou → BUSCA NA IA (mostra agora, grava depois) ───
    if not bib:
        logger.info(f"Perfil não encontrado — buscando na IA: {especie} | porte: {porte}")
        dados_ia = _buscar_referencias_na_ia(especie, porte, sexo)
        if dados_ia:
            return {
                **dados_ia,
                "fonte_ref": f"🔍 Pesquisa automática — será gravada ao finalizar triagem | Espécie: {especie}",
                "validada": False,
                "indisponivel": False,
            }

    # ─── PASSO 4: Sem nada → indisponível ───
    if not bib:
        indisponivel = dict(REFERENCIA_INDISPONIVEL)
        indisponivel["fonte_ref"] = (
            f"⚠️ Sem referência. Espécie: {especie} | Porte: {porte or 'não informado'}"
        )
        return indisponivel

    # ─── PASSO 5: Encontrado no banco ───
    meta = metadados_da_linha(db, bib.id)
    pendente = meta.get("status") == "PENDENTE"

    if pendente:
        n_fontes = len((meta.get("fontes") or {}).get("web", [])) if isinstance(meta.get("fontes"), dict) else 0
        fonte = f"⚠️ PENDENTE — {n_fontes} fonte(s) consultada(s) | Perfil: {bib.especie}"
    else:
        fonte = f"📚 Fonte: {bib.fonte_bibliografica} | Perfil: {bib.especie}"

    if motivo:
        fonte = f"{fonte} | {motivo}"

    return {
        "peso_ref": f"💡 Ref. Peso: {bib.peso_min} - {bib.peso_max} kg",
        "ecc_ref": f"💡 Ideal: {bib.ecc_ideal}",
        "temperatura": f"Normal: Repouso {bib.temp_repouso_min}-{bib.temp_repouso_max}°C | Clínica {bib.temp_clinica_min}-{bib.temp_clinica_max}°C",
        "fc": f"Repouso {bib.fc_repouso_min}-{bib.fc_repouso_max} bpm | Clínica {bib.fc_clinica_min}-{bib.fc_clinica_max} bpm",
        "fr": f"Repouso {bib.fr_repouso_min}-{bib.fr_repouso_max} ir/min | Clínica {bib.fr_clinica_min}-{bib.fr_clinica_max} ir/min",
        "tpc": f"{bib.tpc_ref}",
        "mucosas": f"💡 {bib.mucosas_ref}",
        "fonte_ref": fonte,
        "validada": not pendente,
        "indisponivel": False,
    }

# --------------------------------------------------------------------------- #
# Classificação Manchester
# --------------------------------------------------------------------------- #
@router.post("/avaliar-ia")
def avaliar_triagem_ia(
    dados: AvaliacaoIARequest,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    vitais = (dados.temperatura, dados.frequencia_cardiaca,
              dados.frequencia_respiratoria, dados.tpc_segundos)

    if all(v is None for v in vitais):
        raise HTTPException(
            status_code=422,
            detail="Informe ao menos um sinal vital aferido para avaliar."
        )

    queixa = (dados.queixa_principal or "").strip()
    queixa_norm = _normalizar(queixa)

    # 🚨 TRAVÃO DE SEGURANÇA FÍSICO
    motivos = []
    if dados.temperatura is not None:
        if dados.temperatura >= LIMITE_TEMP_ALTA:
            motivos.append(f"temperatura {dados.temperatura} °C (hipertermia grave)")
        elif dados.temperatura <= LIMITE_TEMP_BAIXA:
            motivos.append(f"temperatura {dados.temperatura} °C (hipotermia grave)")
    if dados.tpc_segundos is not None and dados.tpc_segundos >= LIMITE_TPC_SEG:
        motivos.append(f"TPC {dados.tpc_segundos} s (perfusão comprometida)")
    if any(termo in queixa_norm for termo in TERMOS_EMERGENCIA):
        motivos.append("queixa compatível com inconsciência/decúbito")

    if motivos:
        return {
            "classificacao_risco": "VERMELHO",
            "justificativa": (
                "⚠️ EMERGÊNCIA CLÍNICA: " + "; ".join(motivos) +
                ". Protocolo de Manchester acionado por risco iminente à vida."
            ),
            "origem": "REGRA_FISICA",
        }

    animal = db.query(Animal).filter(Animal.id == dados.animal_id).first() if dados.animal_id else None

    especie = (
        animal.especie.strip().capitalize()
        if animal and animal.especie
        else (dados.especie or "Felino").strip().capitalize()
    )
    sub_especie = animal.sub_especie if animal else dados.sub_especie
    raca = _normalizar_raca(animal.raca if animal else dados.raca)
    porte = animal.porte if animal else dados.porte
    sexo = animal.sexo if animal else None

    perfil_busca = {
        "especie": especie,
        "sub_especie": sub_especie,
        "raca": raca,
        "porte": porte,
        "sexo": sexo,
    }

    bib_ref, _ = _buscar_com_fallback(db, perfil_busca)
    pendente_ref = False
    if bib_ref:
        meta = metadados_da_linha(db, bib_ref.id)
        pendente_ref = meta.get("status") == "PENDENTE"

    prompt = (
        "Você é um médico veterinário especialista em triagem de emergência "
        "(Protocolo Manchester). Classifique o paciente abaixo.\n\n"
        "DADOS DO PACIENTE:\n"
        f"- Espécie/Raça: {especie} / {raca.capitalize() or 'não informada'}\n"
        f"- Porte: {porte or 'não informado'}\n"
        f"- Queixa principal: {queixa or 'não informada'}\n"
        f"- Temperatura: {_valor_ou_nao_aferido(dados.temperatura, '°C')}\n"
        f"- FC: {_valor_ou_nao_aferido(dados.frequencia_cardiaca, 'bpm')}\n"
        f"- FR: {_valor_ou_nao_aferido(dados.frequencia_respiratoria, 'ir/min')}\n"
        f"- TPC: {_valor_ou_nao_aferido(dados.tpc_segundos, 's')}\n"
        f"- Mucosas: {dados.mucosas or 'não informadas'}\n\n"
        f"{_contexto_referencia(bib_ref, pendente_ref)}\n"
        "REGRAS:\n"
        "- 'não aferido' NÃO significa normal; não presuma valores.\n"
        "- Compare com faixas da espécie, nunca com cão/gato genérico.\n"
        "- Em dúvida, escolha o nível mais urgente.\n\n"
        "Retorne JSON: {\"classificacao_risco\": \"VERMELHO|LARANJA|AMARELO|VERDE|AZUL\", \"justificativa\": \"texto\"}"
    )

    try:
        resposta = gerar_json(db, prompt, validar=_validar_avaliacao)
    except IAIndisponivelError as exc:
        logger.error("Avaliação IA indisponível: %s", exc)
        raise HTTPException(status_code=503, detail="IA indisponível — classifique manualmente.")

    return {
        **resposta.dados,
        "origem": "IA",
        "provedor_ia": resposta.provedor,
        "modelo_ia": resposta.modelo,
    }

# --------------------------------------------------------------------------- #
# Persistência — GRAVA REFERÊNCIA AO FINALIZAR TRIAGEM
# --------------------------------------------------------------------------- #
@router.post("/", status_code=status.HTTP_201_CREATED)
def criar_ou_atualizar_triagem(
    dados: TriagemCreate,
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    if not dados.consulta_id:
        raise HTTPException(status_code=400, detail="ID da consulta é obrigatório.")

    # ─── GRAVA REFERÊNCIA NA BIBLIOTECA (SE DISPONÍVEL) ───
    ref_temp = getattr(dados, "_referencia_para_gravar", None)
    if ref_temp and _BIBLIOTECA_DISPONIVEL and BibliotecaReferencia is not None:
        existe = buscar_referencia_oficial(
            db,
            especie=ref_temp.get("especie"),
            raca=ref_temp.get("raca"),
            porte=ref_temp.get("porte"),
            sexo=ref_temp.get("sexo")
        )
        if not existe:
            try:
                db.add(BibliotecaReferencia(**ref_temp))
                logger.info(f"✅ Referência gravada: {ref_temp['especie']} | {ref_temp.get('porte', 'sem porte')}")
            except Exception as e:
                logger.warning(f"⚠️ Não foi possível gravar referência: {e}")

    # ─── Salva triagem normalmente ───
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

    return {
        "mensagem": "Triagem salva com sucesso!",
        "id": triagem_db.id,
        "classificacao_risco": triagem_db.classificacao_risco
    }

@router.get("/fila-triagem")
def listar_fila_triagem(
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    consultas = db.query(Consulta).filter(
        Consulta.status.in_([
            "AGUARDANDO_TRIAGEM", "Aguardando Triagem (Recepção)", "Aguardando Triagem",
            "Chamando para Triagem", "AGUARDANDO_VACINA", "Aguardando Vacina"
        ])
    ).order_by(Consulta.id.asc()).all()

    resultado = []
    for c in consultas:
        animal = db.query(Animal).filter(Animal.id == c.animal_id).first()
        resultado.append({
            "id": c.id,
            "codigo": c.codigo or f"CNS-{c.id:04d}",
            "animal_id": c.animal_id,
            "pet": animal.nome if animal else "Paciente",
            "especie": animal.especie if animal else "-",
            "queixa_principal": c.queixa_principal,
            "peso_atendimento": getattr(c, "peso_atendimento", None),
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
            "ecc": getattr(triagem_db, "ecc", None),
            "temperatura": triagem_db.temperatura,
            "frequencia_cardiaca": triagem_db.frequencia_cardiaca,
            "frequencia_respiratoria": triagem_db.frequencia_respiratoria,
            "tpc_segundos": triagem_db.tpc_segundos,
            "mucosas": triagem_db.mucosas,
            "queixa_principal": triagem_db.queixa_principal,
            "observacoes": getattr(triagem_db, "observacoes", "")
        }

    consulta = db.query(Consulta).filter(Consulta.id == consulta_id).first()
    if not consulta:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")

    return {
        "consulta_id": consulta.id,
        "peso": getattr(consulta, "peso_atendimento", None),
        "ecc": None,
        "temperatura": consulta.temperatura,
        "frequencia_cardiaca": consulta.frequencia_cardiaca,
        "frequencia_respiratoria": consulta.frequencia_respiratoria,
        "tpc_segundos": getattr(consulta, "tpc_segundos", 2),
        "mucosas": getattr(consulta, "mucosas", "Normocoradas"),
        "observacoes": consulta.observacoes or ""
    }