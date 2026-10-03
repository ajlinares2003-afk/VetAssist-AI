import logging
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
from models.referencia_cache import ReferenciaCache
from models.biblioteca_oficial import BibliotecaParametrosOficiais
from services.ia_service import IAIndisponivelError, gerar_json
from services.security import obter_usuario_logado

logger = logging.getLogger("vetassist.triagem")

router = APIRouter(
    prefix="/triagem",
    tags=["Triagem"]
)

# --------------------------------------------------------------------------- #
# Constantes clínicas
# ⚠️ VALIDAR COM O VETERINÁRIO RESPONSÁVEL antes de uso em pacientes reais.
# --------------------------------------------------------------------------- #
CORES_MANCHESTER = ("VERMELHO", "LARANJA", "AMARELO", "VERDE", "AZUL")
ECC_REF_PADRAO = "💡 Ideal: 5/9 (Escala 1 a 9)"

# Travão físico (regras originais mantidas). A FC foi retirada daqui de propósito:
# cortes fixos (ex.: 200 bpm) classificam um Caracal normal em atendimento
# (140-220 bpm) como emergência. A FC é avaliada pela IA com a faixa da espécie.
LIMITE_TEMP_ALTA = 40.5
LIMITE_TEMP_BAIXA = 35.0
LIMITE_TPC_SEG = 3
TERMOS_EMERGENCIA = ("inconsciente", "inconsciencia", "desmai", "decubito")

REFERENCIA_INDISPONIVEL = {
    "peso_ref": "⚠️ Ref. Peso: indisponível",
    "ecc_ref": ECC_REF_PADRAO,
    "temperatura": "Referência indisponível. Consulte o veterinário",
    "fc": "Referência indisponível. Consulte o veterinário",
    "fr": "Referência indisponível. Consulte o veterinário",
    "tpc": "Referência indisponível. Consulte o veterinário",
    "mucosas": "⚠️ Referência indisponível",
    "fonte_ref": "⚠️ Nenhuma referência confiável disponível agora (IA indisponível ou sem dados para esta espécie).",
    "indisponivel": True,
}


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


# --------------------------------------------------------------------------- #
# Auxiliares
# --------------------------------------------------------------------------- #
def _normalizar(texto: str) -> str:
    """minúsculas e sem acentos, para comparar termos da queixa."""
    sem_acento = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in sem_acento if not unicodedata.combining(c))


def _validar_referencias(dados: dict) -> dict:
    for chave in ("peso_ref", "temperatura", "fc", "fr", "tpc", "mucosas"):
        if not isinstance(dados.get(chave), str) or not dados[chave].strip():
            raise ValueError(f"Campo ausente ou inválido na referência: {chave}")
    dados["confiavel"] = dados.get("confiavel") is True
    return dados


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


def _buscar_biblioteca(db: Session, raca: str, especie: str):
    """Do mais específico (raça) para o mais genérico (espécie)."""
    for termo in (raca, especie):
        if termo:
            ref = db.query(BibliotecaParametrosOficiais).filter(
                BibliotecaParametrosOficiais.especie.ilike(f"%{termo}%")
            ).first()
            if ref:
                return ref
    return None


def _contexto_referencia(bib) -> str:
    if not bib:
        return (
            "Não há biblioteca oficial para esta espécie/raça. NÃO presuma valores de gato "
            "ou cão doméstico; avalie com cautela e deixe isso claro na justificativa."
        )
    return (
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
    porte = (dados.porte or "não informado").strip().lower()
    idade_txt = f"{dados.idade} anos" if dados.idade is not None else "não informada"

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

    # 3. CHAMA A IA (provedores e chaves vêm do banco, com fallback automático)
    prompt = (
        "Você é médico veterinário intensivista e semiologista clínico sênior. "
        "Forneça os PARÂMETROS FISIOLÓGICOS de referência na literatura veterinária para o animal abaixo:\n\n"
        f"- Espécie: {especie}\n"
        f"- Sub-espécie/Tipo: {sub_especie}\n"
        f"- Raça: {raca}\n"
        f"- Sexo: {sexo}\n"
        f"- Porte: {porte}\n"
        f"- Idade: {idade_txt}\n\n"
        "REGRAS:\n"
        "- Use ponto como separador decimal e hífen simples (-) entre mínimo e máximo.\n"
        "- FR sempre em ir/min (nunca 'mpm').\n"
        "- NÃO use valores de gato ou cão doméstico para espécies silvestres.\n"
        "- Se não houver dados confiáveis para esta espécie/raça, retorne \"confiavel\": false "
        "e escreva 'Sem referência confiável' nos campos.\n\n"
        "Retorne estritamente um objeto JSON puro com exatamente estas chaves:\n"
        "{\n"
        "  \"peso_ref\": \"💡 Ref. Peso: [min] - [max] kg\",\n"
        "  \"temperatura\": \"Normal: [Repouso: ... | Clínica: ...]\",\n"
        "  \"fc\": \"[Repouso: ... | Clínica: ...] bpm\",\n"
        "  \"fr\": \"[Repouso: ... | Clínica: ...] ir/min\",\n"
        "  \"tpc\": \"texto curto\",\n"
        "  \"mucosas\": \"💡 texto curto\",\n"
        "  \"confiavel\": true\n"
        "}"
    )

    try:
        resposta = gerar_json(db, prompt, validar=_validar_referencias)
    except IAIndisponivelError as exc:
        logger.error("Referências indisponíveis: %s | %s", exc, exc.tentativas)
        # Nunca devolver valores de outra espécie: sinaliza indisponibilidade.
        return dict(REFERENCIA_INDISPONIVEL)

    ref = resposta.dados
    confiavel = ref.pop("confiavel")
    ref["ecc_ref"] = ECC_REF_PADRAO  # ECC ideal = 5/9, definido pela clínica
    # A fonte NÃO é inventada pela IA: informamos o que realmente aconteceu.
    ref["fonte_ref"] = (
        f"🤖 Estimativa gerada por IA ({resposta.provedor}), não validada por veterinário"
        if confiavel else
        "⚠️ Dados limitados na literatura para esta espécie. Confirme com o veterinário"
    )

    # Só guarda em cache o que a IA marcou como confiável.
    if confiavel:
        try:
            db.add(ReferenciaCache(
                especie=especie,
                sub_especie=sub_especie,
                raca=raca,
                porte=porte,
                sexo=sexo,
                peso_ref=ref.get("peso_ref"),
                ecc_ref=ref.get("ecc_ref"),
                temperatura=ref.get("temperatura"),
                fc=ref.get("fc"),
                fr=ref.get("fr"),
                tpc=ref.get("tpc"),
                mucosas=ref.get("mucosas"),
                fonte_ref=ref.get("fonte_ref"),
            ))
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Não foi possível gravar o cache de referência")

    return ref


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

    # 🚨 TRAVÃO DE SEGURANÇA FÍSICO (EMERGÊNCIA DIRETA, sem depender de IA)
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

    especie = (dados.especie or "felino").strip().lower()
    raca = (dados.raca or "").strip().lower()
    bib_ref = _buscar_biblioteca(db, raca, especie)

    prompt = (
        "Você é um médico veterinário especialista em triagem de emergência "
        "(Protocolo Manchester adaptado à medicina veterinária). Classifique o paciente abaixo.\n\n"
        "DADOS DO PACIENTE:\n"
        f"- Espécie/Raça: {especie.capitalize()} / {raca.capitalize() or 'não informada'}\n"
        f"- Queixa principal: {queixa or 'não informada'}\n"
        f"- Temperatura: {_valor_ou_nao_aferido(dados.temperatura, '°C')}\n"
        f"- Frequência cardíaca: {_valor_ou_nao_aferido(dados.frequencia_cardiaca, 'bpm')}\n"
        f"- Frequência respiratória: {_valor_ou_nao_aferido(dados.frequencia_respiratoria, 'ir/min')}\n"
        f"- TPC: {_valor_ou_nao_aferido(dados.tpc_segundos, 's')}\n"
        f"- Mucosas: {dados.mucosas or 'não informadas'}\n\n"
        f"{_contexto_referencia(bib_ref)}\n"
        "REGRAS:\n"
        "- 'não aferido' NÃO significa normal; não presuma valores.\n"
        "- Compare os valores com as faixas da espécie, nunca com as de gato ou cão doméstico.\n"
        "- Em dúvida entre dois níveis, escolha o mais urgente.\n\n"
        "Retorne estritamente um objeto JSON puro com exatamente estas chaves:\n"
        "{\n"
        "  \"classificacao_risco\": \"VERMELHO\" | \"LARANJA\" | \"AMARELO\" | \"VERDE\" | \"AZUL\",\n"
        "  \"justificativa\": \"Justificativa clínica objetiva em português do Brasil, até 3 frases.\"\n"
        "}"
    )

    try:
        resposta = gerar_json(db, prompt, validar=_validar_avaliacao)
    except IAIndisponivelError as exc:
        logger.error("Avaliação por IA indisponível: %s | %s", exc, exc.tentativas)
        # NUNCA assumir 'VERDE' em falha: o paciente pode estar grave.
        raise HTTPException(
            status_code=503,
            detail="IA indisponível no momento. Classifique o paciente manualmente."
        )

    return {
        **resposta.dados,
        "origem": "IA",
        "provedor_ia": resposta.provedor,
        "modelo_ia": resposta.modelo,
    }


# --------------------------------------------------------------------------- #
# Persistência e filas (sem alterações em relação à versão anterior)
# --------------------------------------------------------------------------- #
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
    db: Session = Depends(get_db),
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