"""
services/faixa_etaria.py

Converte a idade (anos) em faixa etária: filhote | adulto | idoso.
A biblioteca guarda uma linha por (perfil, sexo, faixa etária).

⚠️ VALIDAR COM O VETERINÁRIO RESPONSÁVEL. Cortes baseados nas diretrizes AAHA/AAFP (gatos)
e AAHA (cães, por porte). Para espécies que não são cão/gato não existe corte único
(depende da longevidade de cada espécie): usa "adulto" e a idade vai no prompt da pesquisa.
"""
import re
import unicodedata
from typing import Optional

FILHOTE, ADULTO, IDOSO = "filhote", "adulto", "idoso"
FAIXAS = (FILHOTE, ADULTO, IDOSO)
ROTULO = {FILHOTE: "Filhote/jovem", ADULTO: "Adulto", IDOSO: "Idoso/geriátrico"}

# (fim do filhote, início do idoso), em anos
_GATO = (1.0, 11.0)
_CAO_POR_PORTE = {
    "miniatura": (1.0, 10.0), "mini": (1.0, 10.0), "toy": (1.0, 10.0), "pequeno": (1.0, 10.0),
    "medio": (1.0, 9.0), "grande": (1.5, 7.0), "gigante": (2.0, 6.0),
}
_CAO_PADRAO = (1.0, 9.0)


def _norm(t) -> str:
    s = unicodedata.normalize("NFKD", str(t or "").lower())
    return re.sub(r"[^a-z ]+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


def _tipo(especie, sub_especie, nome_cientifico) -> Optional[str]:
    cient = _norm(nome_cientifico)
    txt = f"{_norm(especie)} {_norm(sub_especie)}".split()
    if cient.startswith("felis catus") or "felino" in txt or "gato" in txt:
        # felino silvestre (Caracal etc.) não segue o corte do gato doméstico
        if not cient or cient.startswith("felis catus"):
            return "gato"
    if cient.startswith("canis lupus") or cient.startswith("canis familiaris") \
            or "canino" in txt or "cao" in txt or "cachorro" in txt:
        if not cient or cient.startswith(("canis lupus", "canis familiaris")):
            return "cao"
    return None


def calcular_faixa_etaria(idade_anos, *, especie=None, sub_especie=None,
                          nome_cientifico=None, porte=None) -> str:
    """Sem idade ou espécie sem corte definido -> 'adulto' (e o chamador deve avisar)."""
    if idade_anos is None:
        return ADULTO
    try:
        idade = float(idade_anos)
    except (TypeError, ValueError):
        return ADULTO
    tipo = _tipo(especie, sub_especie, nome_cientifico)
    if tipo == "gato":
        fim_filhote, ini_idoso = _GATO
    elif tipo == "cao":
        fim_filhote, ini_idoso = _CAO_POR_PORTE.get(_norm(porte), _CAO_PADRAO)
    else:
        return ADULTO
    if idade < fim_filhote:
        return FILHOTE
    return IDOSO if idade >= ini_idoso else ADULTO


def faixa_e_aplicavel(especie, sub_especie, nome_cientifico) -> bool:
    """True se a espécie tem corte de idade definido (cão/gato doméstico)."""
    return _tipo(especie, sub_especie, nome_cientifico) is not None