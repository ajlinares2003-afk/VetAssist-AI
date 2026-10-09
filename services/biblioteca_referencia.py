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
  - O NOME CIENTÍFICO do animal (ex.: "Felis catus") é a primeira chave: reduz os perfis
    candidatos aos da mesma espécie biológica antes de olhar raça/porte.
  - Cada perfil pode ter uma linha por (sexo, FAIXA ETÁRIA): filhote | adulto | idoso.
    Linhas antigas, sem a coluna `faixa_etaria`, contam como "adulto".
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


def _sexo_chave(sexo) -> str:
    """
    Normaliza o sexo. O cadastro de animais grava "M"/"F"; a biblioteca usa
    "macho"/"fêmea"/"indiferente". Sem este mapeamento NENHUM animal casava com
    a biblioteca (causa das referências "indisponíveis").
    """
    n = _norm(sexo)
    return {"m": "macho", "f": "femea"}.get(n, n)


# Sinônimos comuns, para "Canis familiaris" e "Canis lupus familiaris" serem a mesma espécie.
SINONIMOS_CIENTIFICOS = {
    "canis familiaris": "canis lupus",
    "canis lupus familiaris": "canis lupus",
    "felis domesticus": "felis catus",
    "felis silvestris catus": "felis catus",
}


def chave_cientifica(nome) -> str:
    """'Felis catus (Linnaeus, 1758)' -> 'felis catus'. Só gênero + espécie, com sinônimos."""
    n = _norm(nome)
    if not n:
        return ""
    completo = " ".join(n.split()[:3])
    if completo in SINONIMOS_CIENTIFICOS:
        return SINONIMOS_CIENTIFICOS[completo]
    binomio = " ".join(n.split()[:2])
    return SINONIMOS_CIENTIFICOS.get(binomio, binomio)


def _faixa_da_linha(linha) -> str:
    return _norm(getattr(linha, "faixa_etaria", None)) or "adulto"


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
    cientificos: set = field(default_factory=set)  # chaves científicas das linhas do perfil
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
        chave = chave_cientifica(getattr(linha, "nome_cientifico", None))
        if chave:
            por_texto[texto].cientificos.add(chave)
    return list(por_texto.values())


def _escolher_descritor(descritores, especie, sub_especie, raca, porte,
                        nome_cientifico=None) -> Optional[Descritor]:
    raca_n = _norm(raca)
    partes_raca = {p for p in _partes(raca) if p not in GENERICAS}

    # 0) Nome científico: fica só com perfis da MESMA espécie biológica. Se nenhum perfil
    #    traz esse nome (biblioteca antiga), segue com todos, como antes.
    chave_cient = chave_cientifica(nome_cientifico)
    if chave_cient:
        mesma_especie = [d for d in descritores if chave_cient in d.cientificos]
        if len(mesma_especie) == 1 and not partes_raca:
            return mesma_especie[0]
        if mesma_especie:
            descritores = mesma_especie

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


def _escolher_linha(descritor: Descritor, sexo, faixa_etaria="adulto"):
    """
    Linha do perfil para (sexo, faixa etária). Só a faixa pedida é aceita: um filhote nunca
    recebe em silêncio a linha de adulto (devolve None; o chamador pesquisa ou avisa).
    """
    faixa = _norm(faixa_etaria) or "adulto"
    por_chave = {(_sexo_chave(getattr(l, "sexo", "")), _faixa_da_linha(l)): l
                 for l in descritor.linhas}
    sexo_n = _sexo_chave(sexo)
    if sexo_n and (sexo_n, faixa) in por_chave:
        return por_chave[(sexo_n, faixa)]
    return por_chave.get(("indiferente", faixa))


def selecionar_referencia(linhas, *, especie, sub_especie, raca, sexo, porte,
                          nome_cientifico=None, faixa_etaria="adulto"):
    """Versão pura (sem banco): recebe as linhas e devolve a escolhida ou None."""
    descritores = _montar_descritores(linhas)
    descritor = _escolher_descritor(descritores, especie, sub_especie, raca, porte,
                                    nome_cientifico)
    if descritor is None:
        return None
    return _escolher_linha(descritor, sexo, faixa_etaria)


def ids_rascunho(db) -> set:
    """IDs das linhas RASCUNHO (pesquisa automática ainda não confirmada pela triagem)."""
    from sqlalchemy import text
    try:
        return set(db.execute(
            text("SELECT id FROM biblioteca_parametros_oficiais WHERE status = 'RASCUNHO'")
        ).scalars().all())
    except Exception:  # coluna ainda não existe (migração pendente)
        db.rollback()
        return set()


def buscar_referencia_oficial(db, *, especie, sub_especie, raca, sexo, porte,
                              nome_cientifico=None, faixa_etaria="adulto",
                              incluir_rascunhos=False):
    """
    Lê a biblioteca (poucas dezenas de linhas) e escolhe a linha do animal.
    Por padrão ignora RASCUNHOS: só entram na biblioteca depois de finalizada a triagem.
    """
    from models.biblioteca_oficial import BibliotecaParametrosOficiais as Bib
    linhas = db.query(Bib).order_by(Bib.id).all()
    if not incluir_rascunhos:
        rascunhos = ids_rascunho(db)
        linhas = [l for l in linhas if l.id not in rascunhos]
    return selecionar_referencia(
        linhas, especie=especie, sub_especie=sub_especie,
        raca=raca, sexo=sexo, porte=porte,
        nome_cientifico=nome_cientifico, faixa_etaria=faixa_etaria,
    )