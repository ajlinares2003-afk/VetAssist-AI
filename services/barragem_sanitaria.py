"""Barragem determinística para doenças de notificação/controle oficial.

Uso na rota (substitui DOENCAS_NOTIFICACAO_COMPULSORIA, ANIMAIS_GRANDE_PORTE
e o bloco "BARRAGEM DETERMINÍSTICA" do consultas.py):

    animal = db.query(Animal).filter(Animal.id == animal_id).first() if animal_id else None
    especie_real = (animal.especie if animal and animal.especie else especie) or ""

    suspeita_extraida, sugestoes_extraidas = extrair_secoes(texto_resposta)
    parecer_oficial = aplicar_barragem(suspeita_extraida, especie_real, texto_resposta)
    if parecer_oficial:
        sugestoes_extraidas = parecer_oficial
"""
import logging
import re
import unicodedata
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def normalizar(texto: Optional[str]) -> str:
    """minúsculas + sem acentos."""
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


# Formas singulares, sem acento (o plural "s" final é removido na comparação).
GRUPOS_ESPECIE = {
    "equideos": {"equino", "equina", "equine", "cavalo", "egua", "potro", "potra",
                 "muar", "mula", "burro", "asinino", "jumento", "jegue"},
    "bovideos": {"bovino", "bovina", "bovine", "boi", "vaca", "bezerro", "bezerra",
                 "novilha", "touro", "bubalino", "bufalo", "bufala"},
    "suinos": {"suino", "suina", "porco", "porca", "leitao"},
    "aves": {"ave", "galinha", "frango", "galo", "peru", "pato", "ganso"},
}

# CONFIRA os testes/normas contra a IN vigente de cada programa antes de usar em produção.
DOENCAS_OFICIAIS = [
    {
        "nome": "Mormo",
        "padrao": r"\bmormo\b|burkholderia mallei",
        "grupos": {"equideos"},
        "exames": "Colheita de amostras para os testes oficiais do PNSE (Fixação de Complemento "
                  "para triagem e confirmatório oficial, conforme a IN vigente).",
    },
    {
        "nome": "Anemia Infecciosa Equina (AIE)",
        "padrao": r"\baie\b|anemia infecciosa",
        "grupos": {"equideos"},
        "exames": "Colheita de amostra para o teste oficial de AIE (Imunodifusão em Ágar Gel - IDGA).",
    },
    {
        "nome": "Brucelose",
        "padrao": r"\bbrucelose\b|\bbrucella\b",
        "grupos": {"bovideos"},
        "exames": "Testes oficiais do PNCEBT (triagem com Antígeno Acidificado Tamponado - AAT e "
                  "confirmatório oficial, conforme o regulamento técnico).",
    },
    {
        "nome": "Tuberculose",
        "padrao": r"\btuberculose\b|mycobacterium (bovis|tuberculosis)",
        "grupos": {"bovideos"},
        "exames": "Testes oficiais do PNCEBT (Teste Cervical Simples ou Comparativo).",
    },
    {
        "nome": "Peste Suína",
        "padrao": r"peste suina",
        "grupos": {"suinos"},
        "exames": "Colheita de amostras exclusivamente pelo serviço veterinário oficial.",
    },
    {
        "nome": "Influenza Aviária / Newcastle",
        "padrao": r"influenza aviaria|newcastle",
        "grupos": {"aves"},
        "exames": "Colheita de amostras exclusivamente pelo serviço veterinário oficial.",
    },
]


def _especie_pertence(especie: str, grupos: set) -> bool:
    palavras = {w[:-1] if w.endswith("s") else w for w in re.findall(r"[a-z]+", normalizar(especie))}
    return any(palavras & GRUPOS_ESPECIE[g] for g in grupos)


def detectar_doenca_oficial(suspeita: str, especie: str) -> Optional[dict]:
    s = normalizar(suspeita)
    for d in DOENCAS_OFICIAIS:
        if re.search(d["padrao"], s) and _especie_pertence(especie, d["grupos"]):
            return d
    return None


def extrair_secoes(texto: str) -> Tuple[str, str]:
    """Extrai (suspeita, sugestoes) tolerando 'Suspeita:', 'SUGESTÕES:', negrito etc."""
    texto = texto or ""
    m = re.search(
        r"SUSPEITA\s*\**\s*:\s*\**(.*?)(?:SUGEST[ÕO]ES\s*\**\s*:\s*\**(.*))?\Z",
        texto, flags=re.S | re.I,
    )
    if not m:
        return "", texto
    suspeita = (m.group(1) or "").replace("**", "").strip()
    sugestoes = (m.group(2) or texto).strip()
    return suspeita, sugestoes


def aplicar_barragem(suspeita: str, especie: str, texto_completo: str = "") -> Optional[str]:
    """Retorna o parecer oficial substituto, ou None se a barragem não se aplica."""
    doenca = detectar_doenca_oficial(suspeita, especie)
    logger.info("barragem: suspeita=%r especie=%r -> %s", suspeita[:80], especie,
                doenca["nome"] if doenca else "não aplicada")
    if not doenca:
        return None

    return (
        f"🛡️ **Alerta de Defesa Sanitária Oficial: suspeita de {doenca['nome']}**\n\n"
        f"• Doença de notificação obrigatória. Trata-se de suspeita levantada pelo copiloto; "
        f"a confirmação depende exclusivamente dos testes oficiais.\n"
        f"• Notifique imediatamente o serviço veterinário oficial (MAPA / órgão estadual de defesa "
        f"sanitária) e interdite o trânsito dos animais da propriedade.\n"
        f"• {doenca['exames']}\n"
        f"• Não há tratamento autorizado para esta doença (fármacos humanos, antimicrobianos e AINEs "
        f"não devem ser prescritos). Em caso de confirmação, a destinação dos positivos "
        f"(sacrifício sanitário/eutanásia) é definida e conduzida pelo serviço oficial.\n"
        f"• Em animais de produção, observe o período de carência para carne e leite."
    )