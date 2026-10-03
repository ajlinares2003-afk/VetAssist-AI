"""
services/biblioteca_referencia.py

Escolhe a linha da `biblioteca_parametros_oficiais` que corresponde ao animal.

Substitui a busca antiga (`ILIKE '%texto%'` + `.first()`), que escolhia linhas
por acaso (ex.: raça "SRD" casava com "Cão de Médio Porte (ex: ..., SRD)").

Princípios:
  - Nunca escolher "a primeira que apareceu". Se houver ambiguidade, devolve None
    e o chamador mostra "referência indisponível" ou usa a estimativa da IA.
  - A escolha é feita em duas etapas: primeiro o PERFIL (texto da coluna `especie`,
    ex.: "Gato Doméstico Comum / SRD") e só depois o SEXO dentro do perfil.
  - Comparação sem acento, sem maiúsculas e por palavra inteira.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

# Raças que NÃO identificam um perfil específico.
GENERICAS = frozenset({
    "srd", "sem raca definida", "sem raca", "nao informada", "nao informado",
    "indefinida", "indefinido", "mestico", "vira lata",
})
# Palavras que não ajudam a distinguir um perfil do outro.
STOP = frozenset({
    "de", "da", "do", "dos", "das", "e", "ex", "domestico", "domesticos",
    "comum", "silvestre", "silvestres", "selvagem", "selvagens", "exotico", "exotica",
    "exoticos", "porte", "raca",
})
SILVESTRE = frozenset({
    "silvestre", "silvestres", "selvagem", "selvagens", "exotico", "exotica", "exoticos",
})
# Porte do cadastro -> palavra usada nos perfis de cães. "Pequeno" fica de fora de
# propósito: não existe perfil de "pequeno porte" e não vamos presumir equivalência.
PORTE_ALIAS = {
    "mini": "miniatura", "miniatura": "miniatura", "toy": "miniatura",
    "medio": "medio", "grande": "grande", "gigante": "gigante",
}


def _norm(texto) -> str:
    """minúsculas, sem acento, só letras/números separados por espaço."""
    if not texto:
        return ""
    sem = unicodedata.normalize("NFKD", str(texto).lower())
    sem = "".join(c for c in sem if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", sem).strip()


def _partes(texto) -> list:
    """Divide 'Caracal - Lince do Deserto' em ['caracal', 'lince do deserto']."""
    if not texto:
        return []
    pedacos = re.split(r"\s+-\s+|/|,|\(|\)", str(texto).lower())
    return [p for p in (_norm(x) for x in pedacos) if p]


def _tokens(texto) -> set:
    """Palavras relevantes reduzidas a 4 letras (felino/felideos -> 'feli')."""
    return {w[:4] for w in _norm(texto).split() if w not in STOP}


@dataclass
class Descritor:
    """Um perfil da biblioteca (um valor distinto da coluna `especie`)."""
    texto: str
    grupo: str
    aliases: set = field(default_factory=set)    # nomes do perfil e termos entre ( ) sem "ex:"
    exemplos: set = field(default_factory=set)   # itens de "(ex: A, B, C)"
    tokens: set = field(default_factory=set)
    palavras: set = field(default_factory=set)   # palavras do nome (para o porte)
    linhas: list = field(default_factory=list)


def _descrever(texto: str, grupo: str) -> Descritor:
    bruto = str(texto or "")
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", bruto)
    nome, dentro = (m.group(1), m.group(2)) if m else (bruto, "")

    d = Descritor(texto=bruto, grupo=_norm(grupo))
    d.aliases = set(_partes(nome))
    if dentro:
        eh_exemplo = re.match(r"^\s*ex\s*:", dentro, flags=re.IGNORECASE) is not None
        itens = set(_partes(re.sub(r"^\s*ex\s*:\s*", "", dentro, flags=re.IGNORECASE)))
        if eh_exemplo:
            d.exemplos = itens
        else:
            d.aliases |= itens
    d.tokens = _tokens(f"{grupo} {nome}")
    d.palavras = set(_norm(nome).split())
    return d


def _montar_descritores(linhas) -> list:
    por_texto = {}
    for linha in linhas:
        texto = getattr(linha, "especie", "") or ""
        if texto not in por_texto:
            por_texto[texto] = _descrever(texto, getattr(linha, "grupo", "") or "")
        por_texto[texto].linhas.append(linha)
    return list(por_texto.values())


def _escolher_descritor(descritores, especie, sub_especie, raca, porte) -> Optional[Descritor]:
    raca_n = _norm(raca)
    partes_raca = {p for p in _partes(raca) if p not in GENERICAS}

    # 1) Raça específica = nome do perfil ou item de "(ex: ...)". Único ou nada.
    if partes_raca:
        for campo in ("aliases", "exemplos"):
            achados = [d for d in descritores if partes_raca & getattr(d, campo)]
            if len(achados) == 1:
                return achados[0]
            if len(achados) > 1:
                return None

    # 2) Sub-espécie = nome do perfil (ou início dele). Ex.: "Gato Doméstico".
    sub_n = _norm(sub_especie)
    if sub_n:
        achados = [d for d in descritores
                   if any(a == sub_n or a.startswith(sub_n + " ") for a in d.aliases)]
        if len(achados) == 1:
            return achados[0]

    # 3) Espécie/sub-espécie (grupo), raça genérica e porte como desempate.
    texto_animal = f"{especie or ''} {sub_especie or ''}"
    tokens_animal = _tokens(texto_animal)
    eh_silvestre = bool(SILVESTRE & set(_norm(texto_animal).split()))

    candidatos = [d for d in descritores if tokens_animal & d.tokens]
    if eh_silvestre:  # animal silvestre nunca herda perfil de doméstico
        candidatos = [d for d in candidatos if "domestic" not in d.grupo]

    if len(candidatos) == 1:
        return candidatos[0]

    if raca_n in GENERICAS:  # "SRD" só vale entre perfis que listam SRD no nome
        marcados = [d for d in candidatos if raca_n in d.aliases]
        if len(marcados) == 1:
            return marcados[0]

    palavra_porte = PORTE_ALIAS.get(_norm(porte))
    if palavra_porte:
        por_porte = [d for d in candidatos if palavra_porte in d.palavras]
        if len(por_porte) == 1:
            return por_porte[0]

    return None  # ambíguo ou desconhecido: não adivinhar


def _escolher_linha(descritor: Descritor, sexo):
    por_sexo = {_norm(getattr(l, "sexo", "")): l for l in descritor.linhas}
    sexo_n = _norm(sexo)
    if sexo_n and sexo_n in por_sexo:
        return por_sexo[sexo_n]
    return por_sexo.get("indiferente")


def selecionar_referencia(linhas, *, especie, sub_especie, raca, sexo, porte):
    """Versão pura (sem banco): recebe as linhas e devolve a escolhida ou None."""
    descritores = _montar_descritores(linhas)
    descritor = _escolher_descritor(descritores, especie, sub_especie, raca, porte)
    if descritor is None:
        return None
    return _escolher_linha(descritor, sexo)


def buscar_referencia_oficial(db, *, especie, sub_especie, raca, sexo, porte):
    """Lê a biblioteca (poucas dezenas de linhas) e escolhe a linha do animal."""
    from models.biblioteca_oficial import BibliotecaParametrosOficiais as Bib
    linhas = db.query(Bib).order_by(Bib.id).all()
    return selecionar_referencia(
        linhas, especie=especie, sub_especie=sub_especie,
        raca=raca, sexo=sexo, porte=porte,
    )