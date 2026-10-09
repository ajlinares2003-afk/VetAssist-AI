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
from services.biblioteca_referencia import buscar_referencia_oficial
from services.faixa_etaria import (
    ADULTO, FILHOTE, ROTULO as ROTULO_FAIXA, calcular_faixa_etaria, faixa_e_aplicavel,
)
from services.nome_cientifico import buscar_nome_cientifico
from services.referencia_auto import metadados_da_linha, pesquisar_e_registrar, promover_rascunho_por_id
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

# Lista de valores considerados raça genérica/SRD
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
    animal_id: Optional[int] = None  # preferido: os dados são lidos do cadastro (fonte única)
    especie: Optional[str] = "Felino"
    sub_especie: Optional[str] = None
    raca: Optional[str] = None
    porte: Optional[str] = None
    sexo: Optional[str] = None
    idade: Optional[float] = None
    nome_cientifico: Optional[str] = None  # 👈 Adicionado para receber o binômio científico

# --------------------------------------------------------------------------- #
# Auxiliares
# --------------------------------------------------------------------------- #
def _normalizar(texto: str) -> str:
    """minúsculas e sem acentos, para comparar termos da queixa."""
    if not texto:
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in sem_acento if not unicodedata.combining(c))

def _normalizar_raca(raca: Optional[str]) -> str:
    """Padroniza o nome da raça para busca."""
    if not raca:
        return ""
    return _normalizar(raca.strip())

def _eh_raca_generica(raca: Optional[str]) -> bool:
    """Verifica se a raça é genérica/SRD."""
    return _normalizar_raca(raca) in RACAS_GENERICAS

def _perfil_do_animal(db: Session, animal_id: Optional[int], payload: dict):
    """
    Perfil do paciente. Com animal_id, TUDO vem do cadastro (espécie, sub-espécie, raça, porte,
    sexo, idade, nome científico); o payload só é usado se o animal não existir.
    """
    animal = db.query(Animal).filter(Animal.id == animal_id).first() if animal_id else None
    origem = (lambda k: getattr(animal, k, None)) if animal else (lambda k: payload.get(k))

    def limpo(v):
        return (str(v).strip() or None) if v is not None else None

    perfil = dict(
        especie=limpo(origem("especie")) or limpo(payload.get("especie")) or "Felino",
        sub_especie=limpo(origem("sub_especie")),
        raca=limpo(origem("raca")),
        porte=limpo(origem("porte")),
        sexo=limpo(origem("sexo")),
        nome_cientifico=limpo(origem("nome_cientifico")),
    )
    return perfil, origem("idade"), animal


def _garantir_nome_cientifico(db: Session, animal, perfil: dict) -> None:
    """
    O nome científico é gerado em segundo plano no cadastro; se a triagem abrir antes (ou se a IA
    falhou na hora), gera agora e grava no animal. A chave da busca é o nome científico.
    """
    if perfil.get("nome_cientifico") or animal is None:
        return
    try:
        nome = buscar_nome_cientifico(db, animal.especie, animal.sub_especie, animal.raca)
        if nome:
            animal.nome_cientifico = nome
            db.commit()
            perfil["nome_cientifico"] = nome
    except Exception:
        db.rollback()
        logger.exception("Falha ao obter o nome científico do animal %s", getattr(animal, "id", "?"))


def _faixa_do_paciente(perfil: dict, idade) -> str:
    return calcular_faixa_etaria(
        idade, especie=perfil["especie"], sub_especie=perfil["sub_especie"],
        nome_cientifico=perfil["nome_cientifico"], porte=perfil["porte"],
    )


def _resolver_referencia(db: Session, perfil: dict, idade, faixa: str, *, pesquisar: bool):
    """
    Única rota de busca da referência (tela de referências E avaliação Manchester):
      1. Biblioteca: nome científico -> perfil -> sexo + faixa etária.
      2. (só se `pesquisar`) pesquisa automática nas fontes oficiais, gravada como RASCUNHO.
      3. Paciente filhote/idoso sem linha própria: cai para a de ADULTO, MARCADA como tal.
    Devolve (linha | None, info).
    """
    info = {"faixa": faixa, "fallback_adulto": False, "motivo": None, "motivo_pesquisa": None}
    incluir = not pesquisar  # a avaliação enxerga os rascunhos que a tela de referências criou

    bib = buscar_referencia_oficial(db, faixa_etaria=faixa, incluir_rascunhos=incluir, **perfil)
    if bib:
        info["motivo"] = "Perfil encontrado na biblioteca"
        return bib, info

    if pesquisar:
        logger.info("Sem perfil — pesquisa automática: %s | %s | porte %s | faixa %s",
                    perfil["especie"], perfil["nome_cientifico"], perfil["porte"], faixa)
        bib, info["motivo_pesquisa"] = pesquisar_e_registrar(
            db, idade=idade, faixa_etaria=faixa, **perfil)
        if bib:
            info["motivo"] = "Perfil pesquisado nas fontes oficiais"
            return bib, info
        logger.warning("Pesquisa sem dados confiáveis: %s | %s",
                       perfil["especie"], info["motivo_pesquisa"])

    if faixa != ADULTO:
        bib = buscar_referencia_oficial(db, faixa_etaria=ADULTO, incluir_rascunhos=incluir, **perfil)
        if bib:
            info.update(fallback_adulto=True, motivo="Sem referência para a faixa etária do paciente")
            return bib, info
    return None, info


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

def _contexto_referencia(bib, pendente: bool = False, aviso_faixa: Optional[str] = None) -> str:
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
    if aviso_faixa:
        aviso += (
            f"ATENÇÃO: {aviso_faixa} Portanto NÃO use estas faixas para dizer se os sinais vitais "
            "estão normais ou alterados; classifique pela queixa e diga na justificativa que os "
            "sinais não puderam ser comparados com a faixa etária do paciente.\n"
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
    perfil, idade_cad, animal = _perfil_do_animal(db, dados.animal_id, dados.model_dump())
    idade = idade_cad if idade_cad is not None else dados.idade
    _garantir_nome_cientifico(db, animal, perfil)
    faixa = _faixa_do_paciente(perfil, idade)

    bib, info = _resolver_referencia(db, perfil, idade, faixa, pesquisar=True)

    if not bib:
        indisponivel = dict(REFERENCIA_INDISPONIVEL)
        indisponivel["fonte_ref"] = (
            "⚠️ Sem referência oficial. "
            f"Motivo: {info['motivo_pesquisa'] or 'perfil não encontrado'} | "
            f"Espécie: {perfil['especie']} | Nome científico: {perfil['nome_cientifico'] or 'não informado'} | "
            f"Porte: {perfil['porte'] or 'não informado'} | Faixa etária: {ROTULO_FAIXA[faixa]}"
        )
        indisponivel.update(faixa_etaria=faixa, nome_cientifico=perfil["nome_cientifico"],
                            peso_ref_aplicavel=(faixa != FILHOTE))
        return indisponivel

    meta = metadados_da_linha(db, bib.id)
    status_ref = meta.get("status")
    rascunho = status_ref == "RASCUNHO"
    pendente = status_ref in ("PENDENTE", "RASCUNHO")
    fontes_meta = meta.get("fontes") if isinstance(meta.get("fontes"), dict) else {}
    n_fontes = len(fontes_meta.get("web", []))
    oficiais = sorted({o.get("fonte") for o in fontes_meta.get("oficiais", []) if o.get("fonte")})
    txt_oficiais = f"; oficiais: {', '.join(oficiais)}" if oficiais else ""

    if rascunho:
        fonte = (
            f"📝 RASCUNHO: pesquisa automática por IA ({n_fontes} fonte(s) consultada(s){txt_oficiais}). "
            "Será gravada na biblioteca ao finalizar a triagem, PENDENTE de validação veterinária | "
            f"Perfil: {bib.especie}"
        )
    elif pendente:
        fonte = (
            f"⚠️ PENDENTE de validação veterinária. "
            f"Pesquisa automática por IA ({n_fontes} fonte(s) consultada(s){txt_oficiais}) | "
            f"Perfil: {bib.especie}"
        )
    else:
        fonte = f"📚 Fonte: {bib.fonte_bibliografica} | Perfil: {bib.especie}"

    idade_txt = f" ({idade} anos)" if idade is not None else ""
    fonte += f" | Faixa etária: {ROTULO_FAIXA[faixa]}{idade_txt}"
    if perfil["nome_cientifico"]:
        fonte += f" | {perfil['nome_cientifico']}"
    if info["fallback_adulto"]:
        fonte += (f" | 🚨 SEM referência para {ROTULO_FAIXA[faixa].lower()}: valores de ADULTO, "
                  "NÃO usar como faixa normal deste paciente")
    elif idade is not None and not faixa_e_aplicavel(
            perfil["especie"], perfil["sub_especie"], perfil["nome_cientifico"]):
        fonte += " | idade não ajustada (espécie sem corte etário definido)"
    if info["motivo"] and not info["fallback_adulto"]:
        fonte += f" | {info['motivo']}"

    peso_aplicavel = faixa != FILHOTE
    peso_txt = (f"💡 Ref. Peso: {bib.peso_min} - {bib.peso_max} kg" if peso_aplicavel else
                f"💡 Peso adulto de ref.: {bib.peso_min} - {bib.peso_max} kg (não se aplica a filhote)")

    return {
        "peso_ref": peso_txt,
        "peso_ref_aplicavel": peso_aplicavel,
        "ecc_ref": f"💡 Ideal: {bib.ecc_ideal}",
        "temperatura": (
            f"Normal: [Repouso: {bib.temp_repouso_min} - {bib.temp_repouso_max} °C | "
            f"Clínica: {bib.temp_clinica_min} - {bib.temp_clinica_max} °C]"
        ),
        "fc": (
            f"[Repouso: {bib.fc_repouso_min} - {bib.fc_repouso_max} bpm | "
            f"Clínica: {bib.fc_clinica_min} - {bib.fc_clinica_max} bpm]"
        ),
        "fr": (
            f"[Repouso: {bib.fr_repouso_min} - {bib.fr_repouso_max} ir/min | "
            f"Clínica: {bib.fr_clinica_min} - {bib.fr_clinica_max} ir/min]"
        ),
        "tpc": f"{bib.tpc_ref}",
        "mucosas": f"💡 {bib.mucosas_ref}",
        "fonte_ref": fonte,
        "faixa_etaria": faixa,
        "nome_cientifico": perfil["nome_cientifico"],
        "ref_adulta_fallback": info["fallback_adulto"],
        "validada": not pendente and not info["fallback_adulto"],
        "rascunho": rascunho,
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

    perfil, idade, _animal = _perfil_do_animal(db, dados.animal_id, dados.model_dump())
    faixa = _faixa_do_paciente(perfil, idade)

    # mesma busca da tela de referências (sem pesquisar de novo: usa biblioteca e rascunhos)
    bib_ref, info_ref = _resolver_referencia(db, perfil, idade, faixa, pesquisar=False)

    pendente_ref = False
    if bib_ref:
        meta = metadados_da_linha(db, bib_ref.id)
        pendente_ref = meta.get("status") in ("PENDENTE", "RASCUNHO")
    aviso_faixa = (
        f"o paciente é {ROTULO_FAIXA[faixa].lower()}, mas só existem faixas de ADULTO para esta espécie."
        if info_ref["fallback_adulto"] else None
    )
    especie = perfil["especie"]
    raca = perfil["raca"] or ""
    porte = perfil["porte"]
    idade_txt = f"{idade} anos" if idade is not None else "não informada"

    prompt = (
        "Você é um médico veterinário especialista em triagem de emergência "
        "(Protocolo Manchester adaptado à medicina veterinária). Classifique o paciente abaixo.\n\n"
        "DADOS DO PACIENTE:\n"
        f"- Espécie/Raça: {especie.capitalize()} / {raca or 'não informada'}\n"
        f"- Nome científico: {perfil['nome_cientifico'] or 'não informado'}\n"
        f"- Porte: {porte or 'não informado'}\n"
        f"- Idade: {idade_txt} (faixa etária: {ROTULO_FAIXA[faixa]})\n"
        f"- Queixa principal: {queixa or 'não informada'}\n"
        f"- Temperatura: {_valor_ou_nao_aferido(dados.temperatura, '°C')}\n"
        f"- Frequência cardíaca: {_valor_ou_nao_aferido(dados.frequencia_cardiaca, 'bpm')}\n"
        f"- Frequência respiratória: {_valor_ou_nao_aferido(dados.frequencia_respiratoria, 'ir/min')}\n"
        f"- TPC: {_valor_ou_nao_aferido(dados.tpc_segundos, 's')}\n"
        f"- Mucosas: {dados.mucosas or 'não informadas'}\n\n"
        f"{_contexto_referencia(bib_ref, pendente_ref, aviso_faixa)}\n"
        "REGRAS:\n"
        "- 'não aferido' NÃO significa normal; não presuma valores.\n"
        "- Compare os valores com as faixas da espécie E da faixa etária informadas, nunca com as de gato ou cão doméstico.\n"
        "- Em dúvida entre dois níveis, escolha o mais urgente.\n\n"
        "Retorne estritamente um objeto JSON puro com exatamente estas chaves:\n"
        "{\n"
        '  "classificacao_risco": "VERMELHO" | "LARANJA" | "AMARELO" | "VERDE" | "AZUL",\n'
        '  "justificativa": "Justificativa clínica objetiva em português do Brasil, até 3 frases."\n'
        "}"
    )

    try:
        resposta = gerar_json(db, prompt, validar=_validar_avaliacao)
    except IAIndisponivelError as exc:
        logger.error("Avaliação por IA indisponível: %s | %s", exc, exc.tentativas)
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
# Persistência e filas
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

    referencia_gravada_id = None
    try:
        animal_id = dados.animal_id
        if not animal_id:
            consulta = db.query(Consulta).filter(Consulta.id == dados.consulta_id).first()
            animal_id = consulta.animal_id if consulta else None
        if animal_id:
            perfil, idade, _animal = _perfil_do_animal(db, animal_id, {})
            faixa = _faixa_do_paciente(perfil, idade)
            bib_usada, info_usada = _resolver_referencia(db, perfil, idade, faixa, pesquisar=False)
            # só promove se a linha é da faixa do paciente (nunca promove o fallback de adulto)
            if bib_usada and not info_usada["fallback_adulto"]:
                referencia_gravada_id = promover_rascunho_por_id(db, bib_usada.id)
    except Exception:
        db.rollback()
        logger.exception("Falha ao gravar a referência pesquisada após a triagem")

    return {
        "mensagem": "Triagem salva com sucesso!",
        "id": triagem_db.id,
        "classificacao_risco": triagem_db.classificacao_risco,
        "referencia_gravada_id": referencia_gravada_id,
    }

@router.get("/fila-triagem")
def listar_fila_triagem(
    db: Session = Depends(get_db),
    usuario_logado = Depends(obter_usuario_logado)
):
    consultas_aguardando = db.query(Consulta).filter(
        Consulta.status.in_([
            "AGUARDANDO_TRIAGEM", "Aguardando Triagem (Recepção)", "Aguardando Triagem",
            "Chamando para Triagem", "AGUARDANDO_VACINA", "Aguardando Vacina"
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