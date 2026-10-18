"""
Barragem sanitária oficial (v4 - 100% Banco de Dados).
Lê a tabela 'defesa_sanitaria' (PostgreSQL/Supabase). Nada de doença fica fixo no código nem no prompt.
"""
import logging
import re
import threading
import time
import unicodedata
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from models.defesa_sanitaria import DoencaNotificacao

logger = logging.getLogger(__name__)

MARCADOR_ALERTA = "Alerta de Defesa Sanitária Oficial"
TTL_CACHE = 300          # segundos; a tabela tem dezenas de linhas e muda raramente
ORGAO_PADRAO = "serviço oficial (MAPA)"
# Espécies para as quais a carência de carne/leite faz sentido (cães, gatos, silvestres etc. ficam de fora)
_GRUPOS_PRODUCAO = {"bovideos", "ruminantes", "suinos", "aves", "equideos", "coelhos", "lagomorfos",
                    "abelhas", "peixes", "crustaceos", "moluscos"}
MIN_SINAIS = 1           # quantos sinais distintos de uma doença são necessários para suspeitar por sinais

# Taxonomia: como o CADASTRO do animal ("Equino", "Canino", "Coelho / Lagomorfo"...) se liga aos
# GRUPOS da tabela ("equideos", "caninos", "lagomorfos"...). Sem isso, "Equino" nunca casaria com "equideos".
_GRUPOS_POR_TERMO: Dict[str, Set[str]] = {
    "equino": {"equideos"}, "equinos": {"equideos"}, "cavalo": {"equideos"}, "egua": {"equideos"},
    "asinino": {"equideos"}, "jumento": {"equideos"}, "muar": {"equideos"}, "mula": {"equideos"},
    "equideo": {"equideos"}, "equideos": {"equideos"},
    "bovino": {"bovideos", "ruminantes"}, "bubalino": {"bovideos", "ruminantes"}, "boi": {"bovideos", "ruminantes"},
    "vaca": {"bovideos", "ruminantes"}, "bovideo": {"bovideos", "ruminantes"}, "bovideos": {"bovideos", "ruminantes"},
    "ovino": {"ruminantes"}, "caprino": {"ruminantes"}, "ovelha": {"ruminantes"}, "cabra": {"ruminantes"},
    "ruminante": {"ruminantes"}, "ruminantes": {"ruminantes"},
    "suino": {"suinos"}, "suinos": {"suinos"}, "porco": {"suinos"}, "javali": {"suinos", "silvestres"},
    "ave": {"aves"}, "aves": {"aves"}, "galinha": {"aves"}, "frango": {"aves"}, "peru": {"aves"},
    "canino": {"caninos"}, "caninos": {"caninos"}, "cao": {"caninos"}, "cachorro": {"caninos"},
    "felino": {"felinos"}, "felinos": {"felinos"}, "gato": {"felinos"},
    "coelho": {"coelhos", "lagomorfos"}, "lagomorfo": {"lagomorfos", "coelhos"}, "lagomorfos": {"lagomorfos", "coelhos"},
    "roedor": {"roedores"}, "roedores": {"roedores"},
    "primata": {"primatas", "silvestres"}, "primatas": {"primatas", "silvestres"}, "macaco": {"primatas", "silvestres"},
    "silvestre": {"silvestres"}, "silvestres": {"silvestres"},
    "reptil": {"silvestres", "repteis"}, "repteis": {"silvestres", "repteis"},
    "abelha": {"abelhas"}, "abelhas": {"abelhas"},
    "peixe": {"peixes"}, "peixes": {"peixes"},
    "crustaceo": {"crustaceos"}, "crustaceos": {"crustaceos"}, "camarao": {"crustaceos"},
    "molusco": {"moluscos"}, "moluscos": {"moluscos"}, "ostra": {"moluscos"},
}

_cache_lock = threading.Lock()
_cache: Dict[str, object] = {"t": 0.0, "dados": []}


def normalizar(texto: Optional[str]) -> str:
    """Retorna minúsculas sem acentos."""
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def _especie_desconhecida(especie: Optional[str]) -> bool:
    n = normalizar(especie).strip()
    return n in {"", "-", "outro", "outros"} or "nao informad" in n


def _grupos_da_especie(especie: str) -> Set[str]:
    grupos: Set[str] = set()
    for w in re.findall(r"[a-z]+", normalizar(especie)):
        grupos |= _GRUPOS_POR_TERMO.get(w, set())
        if w.endswith("s"):
            grupos |= _GRUPOS_POR_TERMO.get(w[:-1], set())
        grupos.add(w)  # permite o cadastro já vir com o nome do grupo
    return grupos


def _animal_de_producao(especie: Optional[str]) -> bool:
    """Espécie desconhecida => True (lado seguro: mantém o lembrete de carência)."""
    return _especie_desconhecida(especie) or bool(_grupos_da_especie(especie or "") & _GRUPOS_PRODUCAO)


def _especie_pertence(especie: str, grupos) -> bool:
    """Grupos vazios = a doença vale para qualquer espécie."""
    if not grupos:
        return True
    return bool(_grupos_da_especie(especie) & {normalizar(g) for g in grupos})


def limpar_cache() -> None:
    """Chame após editar a tabela se quiser efeito imediato (senão vale em até TTL_CACHE segundos)."""
    with _cache_lock:
        _cache["t"] = 0.0


def _carregar(db: Session) -> List[dict]:
    """Lê a tabela inteira (poucas linhas), compila cada regex isoladamente e guarda em cache."""
    agora = time.monotonic()
    with _cache_lock:
        if _cache["dados"] and agora - float(_cache["t"]) < TTL_CACHE:
            return _cache["dados"]  # type: ignore[return-value]
    try:
        linhas = db.query(DoencaNotificacao).all()
    except Exception:
        logger.error("barragem: falha ao ler defesa_sanitaria", exc_info=True)
        try:
            db.rollback()  # senão a sessão fica 'envenenada' e quebra o resto da requisição
        except Exception:
            pass
        with _cache_lock:
            return _cache["dados"]  # type: ignore[return-value]  # último cache bom (ou vazio)

    dados: List[dict] = []
    for d in linhas:
        try:
            rx = re.compile(d.padrao or "", re.IGNORECASE) if d.padrao else None
        except re.error:
            logger.error("barragem: regex inválida em defesa_sanitaria id=%s (%r); linha ignorada", d.id, d.padrao)
            continue
        dados.append({
            "id": d.id, "nome": d.nome, "rx": rx, "grupos": set(d.grupos or []),
            "exames": d.exames, "exames_lista": d.exames_lista or [], "aviso": d.aviso,
            "tratamento": d.tratamento or None, "conduta": list(d.conduta or []),
            "notificacao_confirmada": bool(d.notificacao_confirmada), "sem_carencia": bool(d.sem_carencia),
            "exclusiva": bool(getattr(d, "exclusiva", False)),
            "orgao": (getattr(d, "orgao", None) or "").strip() or None,
            "sinais": [normalizar(x) for x in (getattr(d, "sinais", None) or []) if x],
        })
    with _cache_lock:
        _cache["dados"], _cache["t"] = dados, agora
    return dados


def _sem_ruido(dados: List[dict]) -> List[dict]:
    """Remove doença cujo nome está contido no de outra também achada (ex.: 'Salmoneloses Aviárias' duplicada)."""
    nomes = [normalizar(d["nome"]) for d in dados]
    return [d for i, d in enumerate(dados)
            if not any(j != i and nomes[j] != nomes[i] and nomes[j].startswith(nomes[i]) for j in range(len(dados)))]


def _unicos(itens) -> list:
    vistos, res = set(), []
    for x in itens:
        if x and x not in vistos:
            vistos.add(x)
            res.append(x)
    return res


def _mesclar(doencas: List[dict]) -> dict:
    doencas = _sem_ruido(doencas)
    if len(doencas) == 1:
        return doencas[0]
    exames_lista, vistos = [], set()
    for d in doencas:
        for e in d.get("exames_lista") or []:
            if isinstance(e, dict) and e.get("nome") not in vistos:
                vistos.add(e["nome"])
                exames_lista.append(e)
    avisos = _unicos(d.get("aviso") for d in doencas)
    avisos.append("Mais de uma doença oficial citada no mesmo caso; o serviço oficial deve conduzir o diagnóstico.")
    return {
        "nome": " / ".join(d["nome"] for d in doencas),
        "grupos": set().union(*(d.get("grupos", set()) for d in doencas)),
        "exames": " ".join(_unicos(d.get("exames") for d in doencas)),
        "exames_lista": exames_lista,
        "aviso": " ".join(avisos),
        "tratamento": " ".join(_unicos(d.get("tratamento") for d in doencas)) or None,
        "conduta": _unicos(c for d in doencas for c in d.get("conduta", [])),   # a v3 perdia a conduta (EPI!)
        "notificacao_confirmada": all(d.get("notificacao_confirmada") for d in doencas),  # a mais rígida vence
        "sem_carencia": all(d.get("sem_carencia") for d in doencas),
        "orgao": " e ".join(_unicos(d.get("orgao") for d in doencas)) or None,
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
    if db is None:
        logger.warning("barragem_sanitaria: sessão do banco (db) não fornecida; barragem DESLIGADA nesta chamada.")
        return []
    s = normalizar(suspeita)
    desconhecida = _especie_desconhecida(especie)
    return [d for d in _carregar(db)
            if d["rx"] and d["rx"].search(s) and (desconhecida or _especie_pertence(especie, d["grupos"]))]


def detectar_doenca_oficial(suspeita: str, especie: str, db: Optional[Session] = None) -> Optional[dict]:
    achadas = detectar_doencas_oficiais(suspeita, especie, db=db)
    return _mesclar(achadas) if achadas else None


def doenca_do_parecer(parecer: Optional[str], especie: str, db: Optional[Session] = None) -> Optional[dict]:
    if not parecer or MARCADOR_ALERTA not in parecer:
        return None
    return detectar_doenca_oficial(parecer, especie, db=db)


def exames_oficiais(doenca: Optional[dict]) -> List[dict]:
    if not doenca:
        return []
    return [{
        "nome": e.get("nome", ""),
        "categoria": "Exames laboratoriais",
        "urgencia": "Urgente",
        "justificativa": e.get("justificativa", "Exame oficial de defesa sanitária."),
    } for e in doenca.get("exames_lista") or [] if isinstance(e, dict)]


def doencas_da_especie(especie: str, db: Optional[Session] = None) -> List[str]:
    """Nomes das doenças da espécie (útil para telas/relatórios; NÃO precisa ir ao prompt)."""
    if _especie_desconhecida(especie) or db is None:
        return []
    return [d["nome"] for d in _carregar(db) if d["grupos"] and _especie_pertence(especie, d["grupos"])]


def doenca_por_sinais(texto_clinico: str, especie: str, db: Optional[Session] = None) -> Optional[dict]:
    """Suspeita pelos SINAIS cadastrados na coluna 'sinais' (ex.: head pressing -> Raiva)."""
    if not texto_clinico or _especie_desconhecida(especie) or db is None:
        return None
    s = normalizar(texto_clinico)
    achadas = []
    for d in _carregar(db):
        if not d["sinais"] or not _especie_pertence(especie, d["grupos"]):
            continue
        if sum(1 for sinal in set(d["sinais"]) if sinal in s) >= MIN_SINAIS:
            achadas.append(d)
    return _mesclar(achadas) if achadas else None


def doencas_incompativeis(texto_resposta: str, especie: str, db: Optional[Session] = None) -> List[str]:
    """Doenças marcadas 'exclusiva' que a IA citou para uma espécie fora dos grupos delas."""
    if not texto_resposta or _especie_desconhecida(especie) or db is None:
        return []
    s = normalizar(texto_resposta)
    return [f"{d['nome']} (exclusiva de: {', '.join(sorted(d['grupos']))})"
            for d in _carregar(db)
            if d["exclusiva"] and d["grupos"] and d["rx"] and d["rx"].search(s)
            and not _especie_pertence(especie, d["grupos"])]


def parecer_da_doenca(doenca: dict, por_sinais: bool = False, especie: Optional[str] = None) -> str:
    origem = ("Suspeita clínica levantada a partir dos sinais clínicos informados; a confirmação depende dos exames oficiais."
              if por_sinais else "Trata-se de suspeita levantada; a confirmação depende exclusivamente dos testes oficiais.")
    orgao = doenca.get("orgao") or ORGAO_PADRAO
    if doenca.get("notificacao_confirmada"):
        notificacao = f"• A IN MAPA 50/2013 exige a notificação imediata do caso CONFIRMADO ({orgao}). {origem}"
        isolamento = "• Isole os animais suspeitos e restrinja o trânsito até a orientação oficial."
    else:
        notificacao = f"• Doença de notificação imediata OBRIGATÓRIA (IN MAPA 50/2013). {origem}"
        isolamento = f"• Notifique IMEDIATAMENTE ({orgao}), isole os animais e restrinja o trânsito."
    tratamento = doenca.get("tratamento") or "Não prescreva tratamento farmacológico. A destinação de casos positivos é definida pelo serviço oficial."
    linhas = [f"🛡️ **{MARCADOR_ALERTA}: suspeita de {doenca['nome']}**\n", notificacao, isolamento]
    if doenca.get("exames"):
        linhas.append(f"• {doenca['exames']}")
    linhas += [f"• {tratamento}", "• Não solicite exames complementares fora do fluxo oficial."]
    if not doenca.get("sem_carencia") and _animal_de_producao(especie):
        linhas.append("• Em animais de produção, observe a carência para carne/leite.")
    linhas += [f"• {item}" for item in doenca.get("conduta") or []]
    if doenca.get("aviso"):
        linhas.append(f"• ⚠️ {doenca['aviso']}")
    return "\n".join(linhas)


def aplicar_barragem(suspeita: str, especie: str, texto_completo: str = "", db: Optional[Session] = None,
                     doenca: Optional[dict] = None) -> Optional[str]:
    """Passe 'doenca' já detectada para não consultar/casar duas vezes."""
    doenca = doenca or detectar_doenca_oficial(suspeita, especie, db=db)
    logger.info("barragem: suspeita=%r especie=%r -> %s", (suspeita or "")[:80], especie,
                doenca["nome"] if doenca else "não aplicada")
    return parecer_da_doenca(doenca, especie=especie) if doenca else None