"""
services/fontes_oficiais.py

Matriz de fontes oficiais da clínica (tabela "TOP 3 principais fontes"), por grupo de animal,
e verificação de que a pesquisa da IA realmente consultou essas fontes.

Por que verificar no código: nem o Gemini (Pesquisa do Google) nem o browser_search da Groq
permitem restringir a busca a uma lista de domínios. O prompt PEDE as fontes; este módulo
CONFERE nas páginas que a IA de fato consultou (RespostaIA.fontes) e rejeita a resposta se
nenhuma delas for da lista.

⚠️ Confirme os domínios com a clínica (principalmente ExoCalc e a versão de produção do MSD).
"""
import os
from urllib.parse import urlparse

GRUPO_DOMESTICOS = "Domésticos"
GRUPO_SILVESTRES = "Silvestres/Exóticos"
GRUPO_PRODUCAO = "Produção"

# (nome exibido, domínios aceitos). A ORDEM é a prioridade (1ª, 2ª, 3ª melhor).
FONTES_POR_GRUPO = {
    GRUPO_DOMESTICOS: [
        ("MSD Veterinary Manual", ("msdvetmanual.com",)),
        ("Cornell eClinPath", ("eclinpath.com", "vet.cornell.edu")),
        ("UC Davis (School of Veterinary Medicine)", ("vetmed.ucdavis.edu", "ucdavis.edu")),
    ],
    GRUPO_SILVESTRES: [
        ("ExoCalc", ("exocalc.com", "exocalc.net")),  # ⚠️ confirmar domínio
        ("VIN (Veterinary Information Network)", ("vin.com",)),
        ("Animal Diversity Web", ("animaldiversity.org",)),
    ],
    GRUPO_PRODUCAO: [
        ("MSD Manual Veterinário (Produção)", ("msdvetmanual.com",)),
        ("Embrapa", ("embrapa.br",)),
        ("Iowa State University (Veterinary Medicine)", ("vetmed.iastate.edu", "iastate.edu")),
    ],
}

_TERMOS_PRODUCAO = ("bovino", "suino", "suíno", "ovino", "caprino", "equino", "bubalino",
                    "avestruz", "galinha", "porco", "gado", "cavalo", "coelho de corte")
_TERMOS_DOMESTICOS = ("canino", "felino", "cao", "cão", "gato", "cachorro", "dog", "cat")

# Estrito (padrão): sem nenhuma fonte da lista, a pesquisa é descartada.
# FONTES_OFICIAIS_ESTRITAS=0 aceita a resposta, mas avisa que veio de fontes fora da lista.
ESTRITO = os.getenv("FONTES_OFICIAIS_ESTRITAS", "1") != "0"


def identificar_grupo(especie: str, sub_especie: str = "") -> str:
    texto = f"{especie or ''} {sub_especie or ''}".lower()
    if any(t in texto for t in _TERMOS_PRODUCAO):
        return GRUPO_PRODUCAO
    palavras = set(texto.replace("/", " ").replace("-", " ").split())
    if any(t in palavras for t in _TERMOS_DOMESTICOS):
        return GRUPO_DOMESTICOS
    return GRUPO_SILVESTRES


def texto_prioridade(grupo: str) -> str:
    """Trecho do prompt com as fontes na ordem de prioridade."""
    linhas = []
    for i, (nome, dominios) in enumerate(FONTES_POR_GRUPO[grupo], start=1):
        linhas.append(f"{i}ª opção: {nome} (site: {', '.join(dominios)})")
    return "\n".join(linhas)


def _dominio(url: str) -> str:
    try:
        host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _casa(host: str, dominio: str) -> bool:
    return host == dominio or host.endswith("." + dominio)


def fontes_da_lista(fontes_web: list, grupo: str) -> list:
    """
    Dentre as páginas que a IA consultou ([{titulo, url}]), devolve as que pertencem à lista
    do grupo, com a posição de prioridade. O Gemini devolve links de redirecionamento
    (vertexaisearch...) em que o DOMÍNIO REAL vem no `titulo`; por isso olhamos url e título.
    """
    achadas = []
    for f in fontes_web or []:
        candidatos = {_dominio(str(f.get("url") or "")), _dominio(str(f.get("titulo") or ""))}
        candidatos.discard("")
        for pos, (nome, dominios) in enumerate(FONTES_POR_GRUPO[grupo], start=1):
            if any(_casa(c, d) for c in candidatos for d in dominios):
                achadas.append({"fonte": nome, "prioridade": pos,
                                "titulo": f.get("titulo", ""), "url": f.get("url", "")})
                break
    return achadas


def dominios_vistos(fontes_web: list, limite: int = 6) -> str:
    """Para mensagens de diagnóstico: quais sites a IA consultou de fato."""
    vistos = []
    for f in fontes_web or []:
        d = _dominio(str(f.get("titulo") or "")) or _dominio(str(f.get("url") or ""))
        if d and d not in vistos:
            vistos.append(d)
    return ", ".join(vistos[:limite]) or "nenhum"