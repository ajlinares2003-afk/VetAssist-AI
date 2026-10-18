"""
Barragem sanitária oficial (v3 - 100% Banco de Dados).
Consulta exclusivamente a tabela 'defesa_sanitaria' do PostgreSQL/Supabase.
"""
import logging
import re
import unicodedata
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session
from models.defesa_sanitaria import DoencaNotificacao

logger = logging.getLogger(__name__)

MARCADOR_ALERTA = "Alerta de Defesa Sanitária Oficial"


def normalizar(texto: Optional[str]) -> str:
    """Retorna minúsculas sem acentos."""
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def _especie_desconhecida(especie: Optional[str]) -> bool:
    n = normalizar(especie).strip()
    return n in {"", "-"} or "nao informad" in n


def _especie_pertence(especie: str, grupos: set) -> bool:
    if not grupos:
        return True
    palavras = {w[:-1] if w.endswith("s") else w for w in re.findall(r"[a-z]+", normalizar(especie))}
    grupos_norm = {normalizar(g) for g in grupos}
    return any(p in grupos_norm for p in palavras)


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
        "grupos": set().union(*(d.get("grupos", set()) for d in doencas)),
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
    if db is None or not isinstance(db, Session):
        logger.warning("barragem_sanitaria: Sessão do banco (db) não fornecida.")
        return []

    s = normalizar(suspeita)
    desconhecida = _especie_desconhecida(especie)
    achadas = []

    try:
        regras_db = db.query(DoencaNotificacao).all()
        for d in regras_db:
            if d.padrao and re.search(d.padrao, s, re.IGNORECASE):
                grupos_set = set(d.grupos or [])
                d_dict = {
                    "nome": d.nome,
                    "padrao": d.padrao,
                    "grupos": grupos_set,
                    "exames": d.exames,
                    "exames_lista": d.exames_lista or [],
                    "aviso": d.aviso,
                    "tratamento": d.tratamento,
                    "conduta": d.conduta or [],
                    "notificacao_confirmada": d.notificacao_confirmada,
                    "sem_carencia": d.sem_carencia
                }
                if desconhecida or _especie_pertence(especie, grupos_set):
                    achadas.append(d_dict)
    except Exception as err:
        logger.error(f"Erro ao pesquisar DoencaNotificacao no banco: {err}", exc_info=True)

    return achadas


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
    if _especie_desconhecida(especie) or db is None:
        return []
    try:
        regras_db = db.query(DoencaNotificacao).all()
        return [d.nome for d in regras_db if _especie_pertence(especie, set(d.grupos or []))]
    except Exception as err:
        logger.error(f"Erro em doencas_da_especie no banco: {err}")
        return []


def doenca_por_sinais(texto_clinico: str, especie: str, db: Optional[Session] = None) -> Optional[dict]:
    if not texto_clinico or _especie_desconhecida(especie) or db is None:
        return None
    s = normalizar(texto_clinico)
    sinais_criticos = ["head pressing", "pressionando a cabeca", "andar em circulos", "hiperestesia", "sialorreia", "convulsao", "convulsoes"]
    
    if any(sinal in s for sinal in sinais_criticos):
        return detectar_doenca_oficial("Raiva", especie, db=db)
    return None


def doencas_incompativeis(texto_resposta: str, especie: str) -> List[str]:
    if not texto_resposta or _especie_desconhecida(especie):
        return []
    s = normalizar(texto_resposta)
    erradas = []
    if "febre aftosa" in s and not _especie_pertence(especie, {"bovideos", "ruminantes", "suinos", "bovino", "ovino", "caprino"}):
        erradas.append("Febre Aftosa (exclusiva de biungulados)")
    if "mormo" in s and not _especie_pertence(especie, {"equideos", "equino", "asinino", "muar"}):
        erradas.append("Mormo (exclusivo de equídeos)")
    if "peste suina" in s and not _especie_pertence(especie, {"suinos", "suino", "porco"}):
        erradas.append("Peste Suína (exclusiva de suídeos)")
    if "influenza aviaria" in s and not _especie_pertence(especie, {"aves", "ave"}):
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