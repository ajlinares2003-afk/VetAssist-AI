"""Barragem determinística para doenças de notificação/controle oficial (v4).

Inclui Mormo, AIE, EEB, Raiva, Brucelose, Tuberculose, Febre Aftosa,
Leptospirose, Peste Suína, Influenza Aviária / Newcastle e Carbúnculo Hemático.
"""
import logging
import re
import unicodedata
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

MARCADOR_ALERTA = "Alerta de Defesa Sanitária Oficial"


def normalizar(texto: Optional[str]) -> str:
    """minúsculas + sem acentos."""
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
    "mamiferos": {"canino", "felino", "bovino", "equino", "suino", "ovino", "caprino", "cao", "gato", "boi", "vaca", "cavalo", "porco", "ovelha", "cabra", "rato", "camundongo", "cao selvagem"},
}

# CONFIRA os testes/normas contra a IN vigente de cada programa antes de usar em produção.
DOENCAS_OFICIAIS = [
    {
        "nome": "Mormo",
        "padrao": r"\bmormo\b|burkholderia mallei|\bglanders\b",
        "grupos": {"equideos"},
        "exames": "Colheita de amostras para os testes oficiais do PNSE (Fixação de Complemento "
                  "para triagem e confirmatório oficial, conforme a IN vigente).",
        "exames_lista": [
            {"nome": "Teste oficial de Mormo - triagem (Fixação de Complemento)",
             "justificativa": "Teste oficial do PNSE; colheita conforme orientação do serviço veterinário oficial."},
            {"nome": "Teste confirmatório oficial de Mormo",
             "justificativa": "Realizado em laboratório oficial/credenciado, conforme a IN vigente."},
        ],
        "aviso": "Agente zoonótico de alto risco: use EPI e não colha material para cultura/PCR "
                 "fora do fluxo oficial.",
    },
    {
        "nome": "Anemia Infecciosa Equina (AIE)",
        "padrao": r"\baie\b|\beia\b|anemia infecciosa|infectious an(a)?emia",
        "grupos": {"equideos"},
        "exames": "Colheita de amostra para o teste oficial de AIE (Imunodifusão em Ágar Gel - IDGA).",
        "exames_lista": [
            {"nome": "Teste oficial de AIE (Imunodifusão em Ágar Gel - IDGA)",
             "justificativa": "Único teste oficial de diagnóstico; laboratório credenciado pelo MAPA."},
        ],
    },
    {
        "nome": "Encefalopatia Espongiforme Bovina (EEB / Mal da Vaca Louca)",
        "padrao": r"\beeb\b|vaca louca|encefalopatia espongiforme",
        "grupos": {"bovideos"},
        "exames": "Colheita de tecido do Sistema Nervoso Central (Óbex) realizada exclusivamente pelo Serviço Veterinário Oficial (MAPA) após óbito ou sacrifício sanitário.",
        "exames_lista": [
            {"nome": "Colheita de tecido do SNC (Óbex) pelo Serviço Oficial",
             "justificativa": "Diagnóstico post-mortem oficial para EEB, vedada a colheita clínica rotineira."},
        ],
        "aviso": "Doença de controle estrito da BSE (Mal da Vaca Louca): interdição total da propriedade e isolamento imediato.",
    },
    {
        "nome": "Raiva dos Herbívoros",
        "padrao": r"\braiva\b|rabies|paralisia bovina",
        "grupos": {"ruminantes", "equideos"},
        "exames": "Colheita de amostra de tecido encefálico para diagnóstico laboratorial oficial (Imunofluorescência Direta e Prova Biológica).",
        "exames_lista": [
            {"nome": "Diagnóstico oficial de Raiva (IFD / Prova Biológica)",
             "justificativa": "Executado exclusivamente em laboratório oficial de defesa sanitária."},
        ],
        "aviso": "Zoonose letal de alta gravidade: uso obrigatório de EPI completo (luvas grossas, óculos e máscara) ao manipular animais com sialorréia ou paralisia.",
    },
    {
        "nome": "Brucelose",
        "padrao": r"\bbrucelose\b|\bbrucella\b",
        "grupos": {"bovideos"},
        "exames": "Testes oficiais do PNCEBT (triagem com Antígeno Acidificado Tamponado - AAT e "
                  "confirmatório oficial, conforme o regulamento técnico).",
        "exames_lista": [
            {"nome": "Brucelose - triagem (Antígeno Acidificado Tamponado - AAT)",
             "justificativa": "Teste de triagem do PNCEBT."},
            {"nome": "Brucelose - confirmatório oficial",
             "justificativa": "Conforme o regulamento técnico do PNCEBT."},
        ],
    },
    {
        "nome": "Tuberculose",
        "padrao": r"\btuberculose\b|mycobacterium (bovis|tuberculosis)",
        "grupos": {"bovideos"},
        "exames": "Testes oficiais do PNCEBT (Teste Cervical Simples ou Comparativo).",
        "exames_lista": [
            {"nome": "Tuberculinização oficial (Teste Cervical Simples ou Comparativo)",
             "justificativa": "Teste oficial do PNCEBT."},
        ],
    },
    {
        "nome": "Febre Aftosa",
        "padrao": r"febre aftosa|aftosa",
        "grupos": {"ruminantes", "suinos"},
        "exames": "Inspeção clínica e colheita de epitélio vesicular ou sangue pelo Serviço Veterinário Oficial.",
        "exames_lista": [
            {"nome": "Colheita oficial para diagnóstico de Febre Aftosa",
             "justificativa": "Executada exclusivamente pelo serviço oficial de defesa sanitária."},
        ],
        "aviso": "Doença vesicular de notificação ultra-urgente: alta contagiosidade e impacto severo no comércio internacional.",
    },
    {
        "nome": "Leptospirose",
        "padrao": r"leptospirose|leptospira",
        "grupos": {"mamiferos"},
        "exames": "Testes sorológicos oficiais (Microaglutinação - SMR / MAT) e exames laboratoriais complementares.",
        "exames_lista": [
            {"nome": "Sorologia para Leptospirose (MAT / SMR)",
             "justificativa": "Confirmação laboratorial e monitoramento epidemiológico de zoonose."},
        ],
        "aviso": "Zoonose de relevância em saúde pública: cuidado no manejo de fluidos e urina de animais suspeitos.",
    },
    {
        "nome": "Peste Suína (Clássica / Africana)",
        "padrao": r"peste suina",
        "grupos": {"suinos"},
        "exames": "Colheita de amostras exclusivamente pelo serviço veterinário oficial.",
        "exames_lista": [
            {"nome": "Colheita de amostras pelo serviço veterinário oficial",
             "justificativa": "Nenhum exame deve ser solicitado pelo consultório particular."},
        ],
    },
    {
        "nome": "Influenza Aviária / Newcastle",
        "padrao": r"influenza aviaria|newcastle",
        "grupos": {"aves"},
        "exames": "Colheita de amostras exclusivamente pelo serviço veterinário oficial.",
        "exames_lista": [
            {"nome": "Colheita de amostras pelo serviço veterinário oficial",
             "justificativa": "Nenhum exame deve ser solicitado pelo consultório particular."},
        ],
    },
    {
        "nome": "Carbúnculo Hemático (Antraz)",
        "padrao": r"carbunculo hematico|antraz|bacillus anthracis",
        "grupos": {"ruminantes", "equideos"},
        "exames": "Atenção: É PROIBIDA a necropsia de animais com suspeita de Antraz para evitar a esporulação da bactéria no solo. Colheita de sangue periférico em lâmina restrita ao serviço oficial.",
        "exames_lista": [
            {"nome": "Coleta oficial de sangue em distensão (sob supervisão oficial)",
             "justificativa": "Proibida a abertura da carcaça; colheita estritamente controlada."},
        ],
        "aviso": "Zoonose grave e fulminante: risco altíssimo de infecção humana por esporos ambientais.",
    },
]


def _especie_desconhecida(especie: Optional[str]) -> bool:
    n = normalizar(especie).strip()
    return n in {"", "-"} or "nao informad" in n


def _especie_pertence(especie: str, grupos: set) -> bool:
    palavras = {w[:-1] if w.endswith("s") else w for w in re.findall(r"[a-z]+", normalizar(especie))}
    return any(palavras & GRUPOS_ESPECIE[g] for g in grupos)


def _mesclar(doencas: List[dict]) -> dict:
    """Une doenças concorrentes no mesmo caso em um único registro."""
    if len(doencas) == 1:
        return doencas[0]
    exames_lista, vistos = [], set()
    for d in doencas:
        for e in d["exames_lista"]:
            if e["nome"] not in vistos:
                vistos.add(e["nome"])
                exames_lista.append(e)
    avisos = [d["aviso"] for d in doencas if d.get("aviso")]
    avisos.append("Mais de uma doença oficial citada no mesmo caso; o serviço veterinário "
                  "oficial deve conduzir o diagnóstico diferencial.")
    return {
        "nome": " / ".join(d["nome"] for d in doencas),
        "grupos": set().union(*(d["grupos"] for d in doencas)),
        "exames": " ".join(d["exames"] for d in doencas),
        "exames_lista": exames_lista,
        "aviso": " ".join(avisos),
    }


def detectar_doencas_oficiais(suspeita: str, especie: str) -> List[dict]:
    s = normalizar(suspeita)
    desconhecida = _especie_desconhecida(especie)
    achadas = []
    for d in DOENCAS_OFICIAIS:
        if not re.search(d["padrao"], s):
            continue
        if desconhecida:
            logger.warning("barragem: espécie desconhecida, aplicando %s por precaução", d["nome"])
            achadas.append(d)
        elif _especie_pertence(especie, d["grupos"]):
            achadas.append(d)
    return achadas


def detectar_doenca_oficial(suspeita: str, especie: str) -> Optional[dict]:
    achadas = detectar_doencas_oficiais(suspeita, especie)
    return _mesclar(achadas) if achadas else None


def doenca_do_parecer(parecer: Optional[str], especie: str) -> Optional[dict]:
    if not parecer or MARCADOR_ALERTA not in parecer:
        return None
    return detectar_doenca_oficial(parecer, especie)


def exames_oficiais(doenca: Optional[dict]) -> List[dict]:
    if not doenca:
        return []
    return [
        {"nome": e["nome"], "categoria": "Exames laboratoriais",
         "urgencia": "Urgente", "justificativa": e["justificativa"]}
        for e in doenca["exames_lista"]
    ]


def extrair_secoes(texto: str) -> Tuple[str, str]:
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


def aplicar_barragem(suspeita: str, especie: str, texto_completo: str = "") -> Optional[str]:
    doenca = detectar_doenca_oficial(suspeita, especie)
    logger.info("barragem: suspeita=%r especie=%r -> %s", suspeita[:80], especie,
                doenca["nome"] if doenca else "não aplicada")
    if not doenca:
        return None

    linhas = [
        f"🛡️ **{MARCADOR_ALERTA}: suspeita de {doenca['nome']}**\n",
        "• Doença de notificação obrigatória. Trata-se de suspeita levantada pelo copiloto; "
        "a confirmação depende exclusivamente dos testes oficiais.",
        "• Notifique imediatamente o serviço veterinário oficial (MAPA / órgão estadual de defesa "
        "sanitária), isole os animais e restrinja o trânsito até a orientação oficial "
        "(a interdição formal é ato do serviço oficial).",
        f"• {doenca['exames']}",
        "• Não há tratamento autorizado para esta doença (fármacos humanos, antimicrobianos e AINEs "
        "não devem ser prescritos). Em caso de confirmação, a destinação dos positivos "
        "(sacrifício sanitário/eutanásia) é definida e conduzida pelo serviço oficial.",
        "• Não solicite exames complementares (hemograma, bioquímica, imagem, cultura ou PCR) "
        "fora do fluxo oficial.",
        "• Em animais de produção, observe o período de carência para carne e leite.",
    ]
    if doenca.get("aviso"):
        linhas.append(f"• ⚠️ {doenca['aviso']}")
    return "\n".join(linhas)