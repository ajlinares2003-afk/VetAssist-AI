import logging
import re
import unicodedata
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session
from backend.models.defesa_sanitaria import DoencaNotificacao # Importando seu novo modelo

logger = logging.getLogger(__name__)

MARCADOR_ALERTA = "Alerta de Defesa Sanitária Oficial"

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

def normalizar(texto: Optional[str]) -> str:
    """Retorna minúsculas sem acentos."""
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")

def _especie_desconhecida(especie: Optional[str]) -> bool:
    n = normalizar(especie).strip()
    return n in {"", "-"} or "nao informad" in n

def _especie_pertence(especie: str, grupos: set) -> bool:
    palavras = {w[:-1] if w.endswith("s") else w for w in re.findall(r"[a-z]+", normalizar(especie))}
    return any(palavras & GRUPOS_ESPECIE[g] for g in grupos)

def _mesclar(doencas: List[dict]) -> dict:
    """Une doenças concorrentes detectadas no mesmo caso."""
    if len(doencas) == 1:
        return doencas[0]
    exames_lista, vistos = [], set()
    for d in doencas:
        for e in d["exames_lista"]:
            if e["nome"] not in vistos:
                vistos.add(e["nome"])
                exames_lista.append(e)
    avisos = [d["aviso"] for d in doencas if d.get("aviso")]
    avisos.append("Atenção: Mais de uma doença oficial citada. O serviço veterinário oficial deve conduzir o diagnóstico.")
    
    return {
        "nome": " / ".join(d["nome"] for d in doencas),
        "grupos": set().union(*(d["grupos"] for d in doencas)),
        "exames": " ".join(d["exames"] for d in doencas if d.get("exames")),
        "exames_lista": exames_lista,
        "aviso": " ".join(avisos),
    }

def detectar_doencas_oficiais(suspeita: str, especie: str, db: Session) -> List[dict]:
    """
    Busca todas as regras do MAPA direto do PostgreSQL e checa contra o texto.
    """
    s = normalizar(suspeita)
    desconhecida = _especie_desconhecida(especie)
    achadas = []
    
    # Busca todas as regras cadastradas no banco
    regras_db = db.query(DoencaNotificacao).all()

    for d in regras_db:
        if not re.search(d.padrao, s):
            continue
            
        d_dict = {
            "nome": d.nome,
            "padrao": d.padrao,
            "grupos": set(d.grupos),
            "exames": d.exames,
            "exames_lista": d.exames_lista or [],
            "aviso": d.aviso,
            "tratamento": d.tratamento,
            "conduta": d.conduta or [],
            "notificacao_confirmada": d.notificacao_confirmada,
            "sem_carencia": d.sem_carencia
        }

        if desconhecida:
            logger.warning(f"barragem: espécie desconhecida, aplicando {d.nome} por precaução")
            achadas.append(d_dict)
        elif _especie_pertence(especie, d_dict["grupos"]):
            achadas.append(d_dict)
            
    return achadas

def detectar_doenca_oficial(suspeita: str, especie: str, db: Session) -> Optional[dict]:
    achadas = detectar_doencas_oficiais(suspeita, especie, db)
    return _mesclar(achadas) if achadas else None

def parecer_da_doenca(doenca: dict) -> str:
    """Gera o texto de alerta amarelo baseado no dicionário encontrado."""
    origem = "Trata-se de suspeita levantada; a confirmação depende exclusivamente dos testes oficiais."
    
    if doenca.get("notificacao_confirmada"):
        notificacao = ("• A IN MAPA 50/2013 exige a notificação imediata do caso CONFIRMADO. "
                       "Oriente-se com o serviço veterinário oficial. " + origem)
        isolamento = "• Isole os animais suspeitos e restrinja o trânsito até a orientação oficial."
    else:
        notificacao = f"• Doença de notificação imediata OBRIGATÓRIA (IN MAPA 50/2013). {origem}"
        isolamento = ("• Notifique IMEDIATAMENTE o serviço oficial (MAPA), isole os animais e "
                      "restrinja o trânsito.")
                      
    tratamento = doenca.get("tratamento") or (
        "Não prescreva tratamento farmacológico. A destinação de casos positivos é definida pelo MAPA.")
        
    linhas = [
        f"🛡️ **{MARCADOR_ALERTA}: suspeita de {doenca['nome']}**\n",
        notificacao,
        isolamento,
        f"• {doenca['exames']}",
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

def aplicar_barragem(suspeita: str, especie: str, db: Session) -> Optional[str]:
    """
    Função final a ser chamada pela sua rota FastAPI.
    """
    doenca = detectar_doenca_oficial(suspeita, especie, db)
    
    logger.info(f"barragem: suspeita={suspeita[:30]} especie={especie} -> "
                f"{doenca['nome'] if doenca else 'não aplicada'}")
                
    if not doenca:
        return None

    return parecer_da_doenca(doenca)