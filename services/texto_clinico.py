"""
services/texto_clinico.py

Correções determinísticas em textos escritos pela IA (rede de segurança: o prompt
pede o termo certo, mas o modelo às vezes "inventa" o significado de uma sigla).
"""
import re

TPC_EXTENSO = "Tempo de Preenchimento Capilar"

# "TPC (Tempo de Preensão Cardíaca)" -> "TPC (Tempo de Preenchimento Capilar)"
_TPC_PARENTESES = re.compile(r"\bTPC\s*\(\s*(tempo[^)]*)\)", re.IGNORECASE)
# "Tempo de Preensão Cardíaca (TPC)" -> "Tempo de Preenchimento Capilar (TPC)"
_TPC_ANTES_DA_SIGLA = re.compile(r"\bTempo\s+de\s+[^\W\d_]+\s+[^\W\d_]+(\s*\(\s*TPC\s*\))", re.IGNORECASE)
# Unidade da frequência respiratória: ir/min (nunca "mpm")
_MPM = re.compile(r"\bmpm\b", re.IGNORECASE)


def corrigir_siglas(texto):
    """Devolve o texto com TPC sempre como Tempo de Preenchimento Capilar e FR em ir/min."""
    if not texto or not isinstance(texto, str):
        return texto
    texto = _TPC_PARENTESES.sub(f"TPC ({TPC_EXTENSO})", texto)
    texto = _TPC_ANTES_DA_SIGLA.sub(lambda m: f"{TPC_EXTENSO}{m.group(1)}", texto)
    return _MPM.sub("ir/min", texto)