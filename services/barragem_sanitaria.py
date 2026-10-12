"""Barragem determinística para doenças de notificação/controle oficial (v2).

Mudanças em relação à v1:
  * exames_oficiais(): lista ESTRUTURADA dos únicos exames permitidos (para a tela
    "Solicitação de exames" não depender do LLM).
  * doenca_do_parecer(): permite que qualquer outra rota (ex.: geração de exames)
    descubra, a partir do parecer já salvo, que o caso é de doença oficial.
  * Falha para o lado seguro quando a espécie é desconhecida.
  * Grupos de espécie ampliados (equídeo, bovídeo, asno...).
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
    "suinos": {"suino", "suina", "suideo", "porco", "porca", "leitao"},
    "aves": {"ave", "galinha", "frango", "galo", "peru", "pato", "ganso"},
}

# CONFIRA os testes/normas contra a IN vigente de cada programa antes de usar em produção.
# "exames_lista" é a ÚNICA fonte de exames permitidos para o caso (usada pela tela de exames).
DOENCAS_OFICIAIS = [
    {
        "nome": "Mormo",
        "padrao": r"\bmormo\b|burkholderia mallei",
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
        "padrao": r"\baie\b|anemia infecciosa",
        "grupos": {"equideos"},
        "exames": "Colheita de amostra para o teste oficial de AIE (Imunodifusão em Ágar Gel - IDGA).",
        "exames_lista": [
            {"nome": "Teste oficial de AIE (Imunodifusão em Ágar Gel - IDGA)",
             "justificativa": "Único teste oficial de diagnóstico; laboratório credenciado pelo MAPA."},
        ],
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
        "nome": "Peste Suína",
        "padrao": r"peste suina",
        "grupos": {"suinos"},
        "exames": "Colheita de amostras exclusivamente pelo serviço veterinário oficial.",
        "exames_lista": [
            {"nome": "Colheita de amostras pelo serviço veterinário oficial",
             "justificativa": "Nenhum exame deve ser solicitado pelo consultório."},
        ],
    },
    {
        "nome": "Influenza Aviária / Newcastle",
        "padrao": r"influenza aviaria|newcastle",
        "grupos": {"aves"},
        "exames": "Colheita de amostras exclusivamente pelo serviço veterinário oficial.",
        "exames_lista": [
            {"nome": "Colheita de amostras pelo serviço veterinário oficial",
             "justificativa": "Nenhum exame deve ser solicitado pelo consultório."},
        ],
    },
]


def _especie_desconhecida(especie: Optional[str]) -> bool:
    n = normalizar(especie).strip()
    return n in {"", "-"} or "nao informad" in n


def _especie_pertence(especie: str, grupos: set) -> bool:
    palavras = {w[:-1] if w.endswith("s") else w for w in re.findall(r"[a-z]+", normalizar(especie))}
    return any(palavras & GRUPOS_ESPECIE[g] for g in grupos)


def detectar_doenca_oficial(suspeita: str, especie: str) -> Optional[dict]:
    s = normalizar(suspeita)
    desconhecida = _especie_desconhecida(especie)
    for d in DOENCAS_OFICIAIS:
        if not re.search(d["padrao"], s):
            continue
        if desconhecida:
            # Falha para o lado seguro: espécie ausente não pode desarmar a barragem.
            logger.warning("barragem: espécie desconhecida, aplicando %s por precaução", d["nome"])
            return d
        if _especie_pertence(especie, d["grupos"]):
            return d
    return None


def doenca_do_parecer(parecer: Optional[str], especie: str) -> Optional[dict]:
    """Para rotas que só têm o parecer salvo (ex.: geração de exames).
    Só considera casos em que a barragem já atuou (marcador do alerta)."""
    if not parecer or MARCADOR_ALERTA not in parecer:
        return None
    return detectar_doenca_oficial(parecer, especie)


def exames_oficiais(doenca: Optional[dict]) -> List[dict]:
    """Lista estruturada dos únicos exames permitidos. Ajuste as chaves ao formato
    que a tela 'Solicitação de exames' já consome."""
    if not doenca:
        return []
    return [
        {"nome": e["nome"], "categoria": "Exames laboratoriais",
         "urgencia": "Urgente", "justificativa": e["justificativa"]}
        for e in doenca["exames_lista"]
    ]


def extrair_secoes(texto: str) -> Tuple[str, str]:
    """Extrai (suspeita, sugestoes) tolerando 'Suspeita:', 'SUGESTÕES:', negrito etc."""
    texto = re.sub(r"<think>.*?</think>", "", texto or "", flags=re.S | re.I)  # modelos com raciocínio
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
    """Retorna o parecer oficial substituto, ou None se a barragem não se aplica."""
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