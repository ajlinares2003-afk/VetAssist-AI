"""
Barragem determinística para doenças de notificação/controle oficial (v2 Completa).
Incorporate todas as funções exigidas pelo módulo de consultas e parser de regex.
"""
import logging
import re
import unicodedata
from typing import List, Optional, Tuple, Union
from sqlalchemy.orm import Session
from models.defesa_sanitaria import DoencaNotificacao

logger = logging.getLogger(__name__)

MARCADOR_ALERTA = "Alerta de Defesa Sanitária Oficial"


def normalizar(texto: Optional[str]) -> str:
    """Retorna minúsculas sem acentos."""
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


# Formas singulares, sem acento (o plural "s" final é removido na comparação).
GRUPOS_ESPECIE = {
    "equideos": {"equino", "equina", "equine", "equideo", "cavalo", "egua", "potro", "potra",
                 "muar", "mula", "burro", "asinino", "asno", "jumento", "jegue"},
    "bovideos": {"bovino", "bovina", "bovine", "bovideo", "boi", "vaca", "bezerro", "bezerra",
                 "novilha", "touro", "bubalino", "bufalo", "bufala"},
    "ruminantes": {"bovino", "bovina", "bovine", "bovideo", "boi", "vaca", "bezerro", "bezerra",
                   "novilha", "touro", "bubalino", "bufalo", "bufala", "ovino", "ovina",
                   "carneiro", "ovelha", "cordeiro", "caprino", "caprina", "bode", "cabra", "cabrito"},
    "suinos": {"suino", "suina", "suideo", "porco", "porca", "leitao"},
    "aves": {"ave", "galinha", "frango", "galo", "peru", "pato", "ganso"},
}

DOENCAS_OFICIAIS = [
    {
        "nome": "Raiva dos Herbívoros",
        "padrao": r"\braiva\b|\brabies\b|\bhidrofobia\b",
        "grupos": {"equideos", "ruminantes"},
        "exames": "Coleta de material do SNC pelo serviço oficial para imunofluorescência direta ou prova biológica.",
        "exames_lista": [
            {"nome": "Imunofluorescência Direta (SNC)", "justificativa": "Exame de diagnóstico oficial para Raiva."},
            {"nome": "RT-PCR (SNC)", "justificativa": "Confirmação molecular por laboratório oficial."}
        ],
        "aviso": "Zoonose fatal de notificação compulsória imediata.",
        "tratamento": "Não prescrever tratamento. Isolar o animal e notificar imediatamente a defesa sanitária.",
        "conduta": ["Isolamento rigoroso do animal", "Notificação imediata ao MAPA / Defesa Estadual", "Uso de EPI de alta barreira"],
        "notificacao_confirmada": False,
        "sem_carencia": True
    },
    {
        "nome": "Encefalomielite Equina",
        "padrao": r"\bencefalomielite\b|\barbovirose equina\b",
        "grupos": {"equideos"},
        "exames": "Sorologia (HI/ELISA) ou RT-PCR via laboratório oficial cadastrado.",
        "exames_lista": [
            {"nome": "RT-PCR / Sorologia Encefalomielite Equina", "justificativa": "Pesquisa oficial de arbovirose neurológica."}
        ],
        "aviso": "Doença de notificação compulsória imediata.",
        "tratamento": "Suporte clínico isolado e notificação ao serviço oficial.",
        "conduta": ["Notificação oficial ao MAPA", "Controle de vetores"],
        "notificacao_confirmada": False,
        "sem_carencia": True
    },
    {
        "nome": "Mormo",
        "padrao": r"\bmormo\b|burkholderia mallei|\bglanders\b",
        "grupos": {"equideos"},
        "exames": "Colheita de amostras para os testes oficiais do PNSE (Fixação de Complemento e confirmatório oficial).",
        "exames_lista": [
            {"nome": "Teste oficial de Mormo - triagem (Fixação de Complemento)", "justificativa": "Teste oficial do PNSE."},
            {"nome": "Teste confirmatório oficial de Mormo", "justificativa": "Realizado em laboratório oficial/credenciado."}
        ],
        "aviso": "Agente zoonótico de alto risco: use EPI e não colha material fora do fluxo oficial.",
        "tratamento": "Tratamento proibido em equídeos.",
        "conduta": ["Isolamento do equídeo", "Notificação ao serviço oficial"],
        "notificacao_confirmada": False,
        "sem_carencia": True
    },
    {
        "nome": "Anemia Infecciosa Equina (AIE)",
        "padrao": r"\baie\b|\beia\b|anemia infecciosa|infectious an(a)?emia",
        "grupos": {"equideos"},
        "exames": "Colheita de amostra para o teste oficial de AIE (Imunodifusão em Ágar Gel - IDGA).",
        "exames_lista": [
            {"nome": "Teste oficial de AIE (Imunodifusão em Ágar Gel - IDGA)", "justificativa": "Único teste oficial de diagnóstico; laboratório credenciado pelo MAPA."}
        ],
        "aviso": "Doença de notificação compulsória em equídeos.",
        "tratamento": "Não há tratamento autorizado. Eutanásia ou isolamento permanente conforme legislação.",
        "conduta": ["Isolamento do equídeo", "Notificação ao serviço oficial"],
        "notificacao_confirmada": False,
        "sem_carencia": True
    },
    {
        "nome": "Brucelose Bovina",
        "padrao": r"\bbrucelose\b|\bbrucella\b",
        "grupos": {"bovideos", "ruminantes"},
        "exames": "Testes oficiais do PNCEBT (triagem com Antígeno Acidificado Tamponado - AAT e confirmatório oficial).",
        "exames_lista": [
            {"nome": "Brucelose - triagem (Antígeno Acidificado Tamponado - AAT)", "justificativa": "Teste de triagem do PNCEBT."},
            {"nome": "Brucelose - confirmatório oficial", "justificativa": "Conforme o regulamento técnico do PNCEBT."}
        ],
        "aviso": "Zoonose de notificação compulsória sob o PNCEBT.",
        "tratamento": "Tratamento proibido em bovinos/bubalinos.",
        "conduta": ["Eutanásia/Abate sanitário dos positivos", "Notificação oficial"],
        "notificacao_confirmada": False,
        "sem_carencia": False
    },
    {
        "nome": "Tuberculose Bovina",
        "padrao": r"\btuberculose\b|mycobacterium (bovis|tuberculosis)",
        "grupos": {"bovideos", "ruminantes"},
        "exames": "Testes oficiais do PNCEBT (Teste Cervical Simples ou Comparativo).",
        "exames_lista": [
            {"nome": "Tuberculinização oficial (Teste Cervical Simples ou Comparativo)", "justificativa": "Teste oficial do PNCEBT."}
        ],
        "aviso": "Zoonose de notificação compulsória sob o PNCEBT.",
        "tratamento": "Tratamento proibido em bovinos/bubalinos.",
        "conduta": ["Eutanásia/Abate sanitário dos reagentes", "Notificação oficial"],
        "notificacao_confirmada": False,
        "sem_carencia": False
    },
    {
        "nome": "Febre Aftosa",
        "padrao": r"\bfebre aftosa\b|\baftosa\b",
        "grupos": {"bovideos", "ruminantes", "suinos"},
        "exames": "Isolamento viral ou RT-PCR pelo LFDA/MAPA.",
        "exames_lista": [
            {"nome": "RT-PCR LFDA/MAPA", "justificativa": "Coleta exclusiva pelo serviço oficial."}
        ],
        "aviso": "Doença vesicular de notificação compulsória imediata.",
        "tratamento": "Proibido qualquer tratamento. Interdição imediata da propriedade.",
        "conduta": ["Interdição da propriedade", "Notificação imediata ao serviço oficial"],
        "notificacao_confirmada": False,
        "sem_carencia": False
    }
]


def _especie_desconhecida(especie: Optional[str]) -> bool:
    n = normalizar(especie).strip()
    return n in {"", "-"} or "nao informad" in n


def _especie_pertence(especie: str, grupos: set) -> bool:
    palavras = {w[:-1] if w.endswith("s") else w for w in re.findall(r"[a-z]+", normalizar(especie))}
    return any(palavras & GRUPOS_ESPECIE.get(g, set()) for g in grupos)


def _mesclar(doencas: List[dict]) -> dict:
    if len(doencas) == 1:
        return doencas[0]
    exames_lista, vistos = [], set()
    for d in doencas:
        for e in d.get("exames_lista", []):
            if isinstance(e, dict) and e.get("nome") not in vistos:
                vistos.add(e["nome"])
                exames_lista.append(e)
    avisos = [d["aviso"] for d in doencas if d.get("aviso")]
    avisos.append("Mais de uma doença oficial citada no mesmo caso; o serviço oficial deve conduzir o diagnóstico.")
    return {
        "nome": " / ".join(d["nome"] for d in doencas),
        "grupos": set().union(*(d["grupos"] for d in doencas)),
        "exames": " ".join(d["exames"] for d in doencas if d.get("exames")),
        "exames_lista": exames_lista,
        "aviso": " ".join(avisos),
    }


def extrair_secoes(texto: str) -> Tuple[str, str]:
    """Extrai (suspeita, sugestoes) tolerando 'Suspeita:', 'SUGESTÕES:', negrito e tags <think>."""
    texto = re.sub(r"<think>.*?</think>", "", texto or "", flags=re.S | re.I)
    m = re.search(
        r"SUSPEITA(?:\s+DIAGN[ÓO]STICA)?\s*\**\s*:\s*\**(.*?)(?:SUGEST[ÕO]ES\s*\**\s*:\s*\**(.*))?\Z",
        texto, flags=re.S | re.I,
    )
    if not m:
        return "", texto
    suspeita = (m.group(1) or "").replace("**", "").strip()
    sugestoes = (m.group(2) or texto).strip()
    return suspeita, sugestoes


def detectar_doencas_oficiais(suspeita: str, especie: str, db: Optional[Session] = None) -> List[dict]:
    s = normalizar(suspeita)
    desconhecida = _especie_desconhecida(especie)
    achadas = []

    if db is not None and isinstance(db, Session):
        try:
            regras_db = db.query(DoencaNotificacao).all()
            for d in regras_db:
                if d.padrao and re.search(d.padrao, s, re.IGNORECASE):
                    d_dict = {
                        "nome": d.nome,
                        "padrao": d.padrao,
                        "grupos": set(d.grupos or []),
                        "exames": d.exames,
                        "exames_lista": d.exames_lista or [],
                        "aviso": d.aviso,
                        "tratamento": d.tratamento,
                        "conduta": d.conduta or [],
                        "notificacao_confirmada": d.notificacao_confirmada,
                        "sem_carencia": d.sem_carencia
                    }
                    if desconhecida or _especie_pertence(especie, d_dict["grupos"]):
                        achadas.append(d_dict)
            if achadas:
                return achadas
        except Exception as err:
            logger.warning(f"Erro ao pesquisar DoencaNotificacao no DB: {err}")

    for d in DOENCAS_OFICIAIS:
        if re.search(d["padrao"], s, re.IGNORECASE):
            if desconhecida or _especie_pertence(especie, d["grupos"]):
                achadas.append(d)

    return achadas


def detectar_doenca_oficial(suspeita: str, especie: str, db: Optional[Session] = None) -> Optional[dict]:
    achadas = detectar_doencas_oficiais(suspeita, especie, db=db)
    return _mesclar(achadas) if achadas else None


def doenca_do_parecer(parecer: Optional[str], especie: str) -> Optional[dict]:
    if not parecer or MARCADOR_ALERTA not in parecer:
        return None
    return detectar_doenca_oficial(parecer, especie)


def exames_oficiais(doenca: Optional[dict]) -> List[dict]:
    if not doenca:
        return []
    res = []
    for e in doenca.get("exames_lista", []):
        if isinstance(e, dict):
            res.append({
                "nome": e.get("nome", ""),
                "categoria": "Exames laboratoriais",
                "urgencia": "Urgente",
                "justificativa": e.get("justificativa", "Exame oficial de defesa sanitária.")
            })
    return res


def doencas_da_especie(especie: str, db: Optional[Session] = None) -> List[str]:
    if _especie_desconhecida(especie):
        return []
    res = []
    if _especie_pertence(especie, {"equideos"}):
        res.extend(["Raiva dos Herbívoros", "Encefalomielite Equina", "Anemia Infecciosa Equina", "Mormo"])
    if _especie_pertence(especie, {"bovideos", "ruminantes"}):
        res.extend(["Febre Aftosa", "Brucelose Bovina", "Tuberculose Bovina", "Raiva dos Herbívoros"])
    if _especie_pertence(especie, {"suinos"}):
        res.extend(["Peste Suína Clássica", "Peste Suína Africana"])
    if _especie_pertence(especie, {"aves"}):
        res.extend(["Influenza Aviária", "Doença de Newcastle"])
    return res


def doenca_por_sinais(texto_clinico: str, especie: str, db: Optional[Session] = None) -> Optional[dict]:
    if not texto_clinico or _especie_desconhecida(especie):
        return None
    s = normalizar(texto_clinico)
    sinais_raiva = ["head pressing", "pressionando a cabeca", "andar em circulos", "hiperestesia", "sialorreia", "convulsao", "convulsoes"]
    if any(sinal in s for sinal in sinais_raiva) and _especie_pertence(especie, {"equideos", "ruminantes"}):
        return {
            "nome": "Raiva dos Herbívoros",
            "grupos": {"equideos", "ruminantes"},
            "exames": "Coleta e envio de tecido do SNC pelo serviço veterinário oficial.",
            "exames_lista": [{"nome": "Imunofluorescência Direta (SNC)", "justificativa": "Diagnóstico sorológico/histopatológico oficial."}],
            "aviso": "Zoonose fatal de notificação compulsória imediata.",
            "tratamento": "Tratamento proibido. Isolar o animal e notificar imediatamente a defesa sanitária.",
            "conduta": ["Isolamento imediato do animal", "Notificação compulsória ao MAPA / Defesa Estadual", "Uso obrigatório de EPI completo"],
            "notificacao_confirmada": False,
            "sem_carencia": True
        }
    return None


def doencas_incompativeis(texto_resposta: str, especie: str) -> List[str]:
    if not texto_resposta or _especie_desconhecida(especie):
        return []
    s = normalizar(texto_resposta)
    erradas = []
    if "febre aftosa" in s and not _especie_pertence(especie, {"bovideos", "ruminantes", "suinos"}):
        erradas.append("Febre Aftosa (exclusiva de biungulados)")
    if "mormo" in s and not _especie_pertence(especie, {"equideos"}):
        erradas.append("Mormo (exclusivo de equídeos)")
    if "peste suina" in s and not _especie_pertence(especie, {"suinos"}):
        erradas.append("Peste Suína (exclusiva de suídeos)")
    if "influenza aviaria" in s and not _especie_pertence(especie, {"aves"}):
        erradas.append("Influenza Aviária (exclusiva de aves)")
    return erradas


def parecer_da_doenca(doenca: dict, por_sinais: bool = False) -> str:
    origem = ("Suspeita clínica levantada a partir dos sinais clínicos informados; a confirmação depende dos exames oficiais."
              if por_sinais else "Trata-se de suspeita levantada; a confirmação depende exclusivamente dos testes oficiais.")
    
    if doenca.get("notificacao_confirmada"):
        notificacao = f"• A IN MAPA 50/2013 exige a notificação imediata do caso CONFIRMADO. {origem}"
        isolamento = "• Isole os animais suspeitos e restrinja o trânsito até a orientação oficial."
    else:
        notificacao = f"• Doença de notificação imediata OBRIGATÓRIA (IN MAPA 50/2013). {origem}"
        isolamento = "• Notifique IMEDIATAMENTE o serviço oficial (MAPA), isole os animais e restrinja o trânsito."
                      
    tratamento = doenca.get("tratamento") or "Não prescreva tratamento farmacológico. A destinação de casos positivos é definida pelo MAPA."
        
    linhas = [
        f"🛡️ **{MARCADOR_ALERTA}: suspeita de {doenca['nome']}**\n",
        notificacao,
        isolamento,
        f"• {doenca.get('exames', '')}",
        f"• {tratamento}",
        "• Não solicite exames complementares fora do fluxo oficial.",
    ]
    if not doenca.get("sem_carencia"):
        linhas.append("• Em animais de produção, observe a carência para carne/leite.")
    for item in doenca.get("conduta", []):
        linhas.append(f"• {item}")
    if doenca.get("aviso"):
        linhas.append(f"• ⚠️ {doenca['aviso']}")
        
    return "\n".join(linhas)


def aplicar_barragem(suspeita: str, especie: str, texto_completo: str = "", db: Optional[Session] = None) -> Optional[str]:
    doenca = detectar_doenca_oficial(suspeita, especie, db=db)
    logger.info("barragem: suspeita=%r especie=%r -> %s", suspeita[:80], especie,
                doenca["nome"] if doenca else "não aplicada")
    if not doenca:
        return None
    return parecer_da_doenca(doenca)