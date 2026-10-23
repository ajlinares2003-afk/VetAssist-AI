"""
routers/atendimento_ia.py

Receita médica e solicitação de exames sugeridas pela IA a partir do parecer do
Copiloto Clínico (tela de Atendimentos).

Princípios de segurança (a IA é APOIO; quem prescreve é o veterinário):
  - A IA devolve a dose em mg/kg e a concentração do produto; a CONTA (mg totais,
    mL, fração de comprimido) é feita aqui, em Python, nunca pela IA.
  - Sem peso informado não se calcula dose (o sistema avisa em vez de chutar).
  - Medicamentos sabidamente perigosos para a espécie são removidos e informados.
  - Medicamentos possivelmente controlados recebem aviso.
  - Espécie exótica sem dose bem estabelecida: a IA deve deixar a dose em aberto.

Lembre de registrar no main.py:  app.include_router(atendimento_ia.router)
"""
import logging
import math
import re
import unicodedata
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from database import get_db
from models.animais import Animal
from services.barragem_sanitaria import detectar_doenca_oficial, doenca_do_parecer
from services.ia_service import IAIndisponivelError, gerar_json
from services.security import exigir_perfil
from services.texto_clinico import corrigir_siglas

logger = logging.getLogger("vetassist.atendimento_ia")

router = APIRouter(prefix="/atendimento-ia", tags=["Atendimento (receita e exames com IA)"])

PERFIS_CLINICOS = ["ADMIN", "VETERINARIO"]

# --------------------------------------------------------------------------- #
# Tabelas de apoio
# --------------------------------------------------------------------------- #
TIPO_USO_ROTULO = {
    "HUMANO": "Uso Humano (Farmácia / Drogaria)",
    "VETERINARIO": "Uso Veterinário (Pet Shop / Agropecuária)",
    "CLINICA": "Uso Clínico (aplicação na clínica)",
    "A_CONFIRMAR": "Disponibilidade a confirmar",
}

FREQUENCIA_ROTULO = {
    24: "A cada 24 horas (SID)",
    12: "A cada 12 horas (BID)",
    8: "A cada 8 horas (TID)",
    6: "A cada 6 horas (QID)",
}

# Princípios ativos que NÃO entram na receita automática, por grupo de espécie.
PROIBIDOS_POR_GRUPO = {
    "felino": ("paracetamol", "acetaminofeno", "permetrina", "ibuprofeno", "naproxeno"),
    "canino": ("paracetamol", "acetaminofeno", "ibuprofeno", "naproxeno"),
    "outro": ("paracetamol", "acetaminofeno", "ibuprofeno", "naproxeno"),
}

# Possivelmente sujeitos a controle especial (Portaria SVS/MS 344/98). Só um aviso:
# a lista vigente deve ser conferida pelo veterinário.
POSSIVELMENTE_CONTROLADOS = (
    "tramadol", "morfina", "metadona", "fentanil", "codeina", "petidina", "meperidina",
    "buprenorfina", "oxicodona", "hidromorfona", "remifentanil", "sufentanil", "alfentanil",
    "tapentadol", "cetamina", "ketamina", "tiletamina", "zolazepam", "diazepam", "midazolam",
    "clonazepam", "alprazolam", "lorazepam", "clorazepato", "fenobarbital", "pentobarbital",
    "amitriptilina", "clomipramina", "fluoxetina", "sertralina", "paroxetina", "carbamazepina",
)

# Fármacos sem eficácia por via ORAL na espécie: o item oral é removido e o veterinário decide.
ORAL_INEFICAZ_POR_GRUPO = {
    "canino": {
        "buprenorfina": "por via oral deglutida a buprenorfina tem baixa biodisponibilidade em cães",
    },
}

# Plausibilidade de apresentação/volume (só alerta; quem decide é o veterinário). Cães e gatos.
LIMITE_VOLUME_ORAL_ML = 15.0
LIMITE_VOLUME_INJETAVEL_ML = 5.0
MAX_DIAS_CLINICA = 3        # aplicação na clínica por mais dias que isso pede confirmação
MAX_DIAS_CONTROLADO = 7     # controlado por mais dias que isso pede confirmação

ANTIMICROBIANOS = (
    "amoxicilina", "ampicilina", "penicilina", "clavulan", "cefalexina", "cefadroxila", "cefovecina",
    "cefpodoxima", "ceftiofur", "enrofloxacino", "marbofloxacino", "ciprofloxacino", "pradofloxacino",
    "orbifloxacino", "doxiciclina", "tetraciclina", "oxitetraciclina", "metronidazol", "clindamicina",
    "lincomicina", "azitromicina", "claritromicina", "gentamicina", "amicacina", "sulfadiazina",
    "trimetoprima", "florfenicol", "rifampicina", "tilosina", "eritromicina",
)

# Princípios ativos cuja lista na Portaria SVS/MS 344/98 é conhecida: corrige a IA quando ela erra a lista.
# Confira sempre a norma vigente (as listas são atualizadas).
LISTA_CONHECIDA = {"buprenorfina": "A1", "morfina": "A1", "metadona": "A1", "fentanil": "A1",
                   "petidina": "A1", "meperidina": "A1"}

# Quadros reprodutivos em cães que exigem triagem de Brucella canis (zoonose).
MARCADORES_REPRODUCAO = ("epididim", "orquite", "piospermia", "infertilidade", "falha de concepcao",
                         "falhas de concepcao", "aborto", "abortamento")

# Guarda-corpo determinístico: o que o prompt proíbe, o código também impõe.
AINES = (
    "meloxicam", "carprofeno", "firocoxibe", "cetoprofeno", "flunixina", "flunixin", "piroxicam",
    "tenoxicam", "diclofenaco", "nimesulida", "robenacoxibe", "etodolaco", "ketoprofeno",
)
CORTICOIDES = (
    "prednisolona", "prednisona", "dexametasona", "betametasona", "metilprednisolona",
    "triancinolona", "hidrocortisona", "deflazacorte",
)
# Penicilinas, lincosamidas e cefalosporinas: enterotoxemia por via ORAL em lagomorfos e roedores.
ANTIBIOTICOS_RISCO_ORAL_ROEDORES = (
    "amoxicilina", "ampicilina", "penicilina", "clavulan", "clindamicina", "lincomicina",
    "cefalexina", "cefadroxila", "cefovecina", "cefpodoxima", "ceftiofur", "cefalosporina",
)
RADICAIS_LAGOMORFO_ROEDOR = (
    "coelh", "lagomorf", "oryctolagus", "porquinh", "cobaia", "cavia", "chinchila",
    "hamster", "gerbil", "jerbo", "roedor", "rato", "camundong", "esquilo",
)
# Contexto em que AINE é proibido (intoxicação aguda, desidratação grave, falência renal/hepática).
MARCADORES_CONTRAINDICACAO_AINE = (
    "intoxicacao", "envenenamento", "desidratacao grave", "desidratacao severa",
    "insuficiencia renal", "falencia renal", "doenca renal", "insuficiencia hepatica",
    "falencia hepatica", "hepatopatia",
)

LISTAS_CONTROLE = {"A1", "A2", "A3", "B1", "B2", "C1", "C2", "C3", "C4", "C5"}

# Marcadores usados para guardar recomendações e quantidade dentro de item_prescricao.observacoes,
# sem exigir novas colunas. O histórico separa de volta; em outras telas o texto continua legível.
SEP_QUANTIDADE = "\n\nQuantidade: "
SEP_RECOMENDACOES = "\n\nRecomendações adicionais: "

_UNIDADES_EXT = ["zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez",
                 "onze", "doze", "treze", "quatorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove"]
_DEZENAS_EXT = ["", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa"]
_CENTENAS_EXT = ["", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos", "seiscentos",
                 "setecentos", "oitocentos", "novecentos"]


def _extenso(n: int) -> str:
    """Número por extenso (receituário de controle especial pede a quantidade em algarismos e por extenso)."""
    if n < 20:
        return _UNIDADES_EXT[n]
    if n < 100:
        d, u = divmod(n, 10)
        return _DEZENAS_EXT[d] + (f" e {_UNIDADES_EXT[u]}" if u else "")
    if n == 100:
        return "cem"
    if n < 1000:
        c, r = divmod(n, 100)
        return _CENTENAS_EXT[c] + (f" e {_extenso(r)}" if r else "")
    return str(n)


def _verdadeiro(valor) -> bool:
    return valor is True or str(valor).strip().lower() in ("true", "sim", "1")


def _lista_controle_valida(valor) -> Optional[str]:
    lista = str(valor or "").strip().upper().replace(" ", "")
    return lista if lista in LISTAS_CONTROLE else None


def _empacotar_observacoes(observacoes, recomendacoes, quantidade) -> Optional[str]:
    base = str(observacoes or "").strip()
    if quantidade and str(quantidade).strip():
        base += SEP_QUANTIDADE + str(quantidade).strip()[:200]
    rec = str(recomendacoes or "").strip()
    if rec:
        orcamento = 1000 - len(base) - len(SEP_RECOMENDACOES)   # cabe em colunas de 1000 caracteres
        if orcamento > 0:
            base += SEP_RECOMENDACOES + rec[:orcamento]
    return base or None


def _desempacotar_observacoes(texto) -> dict:
    texto = str(texto or "")
    recomendacoes = quantidade = ""
    if SEP_RECOMENDACOES in texto:
        texto, recomendacoes = texto.split(SEP_RECOMENDACOES, 1)
    if SEP_QUANTIDADE in texto:
        texto, quantidade = texto.split(SEP_QUANTIDADE, 1)
    return {"observacoes": texto.strip(), "recomendacoes": recomendacoes.strip(), "quantidade": quantidade.strip()}


def _quantidade_total(dose, peso, conc_valor, conc_unidade, freq_horas, dias, clinica: bool = False) -> Optional[str]:
    """Quantidade a dispensar para o tratamento completo (só quando há dados para calcular)."""
    conc, h, d = _numero(conc_valor), _numero(freq_horas), _numero(dias)
    if dose is None or peso is None or conc is None or h is None or d is None:
        return None
    tomadas = max(1, math.ceil(d * 24 / h))
    por_tomada_mg = dose * peso
    if clinica:   # aplicado na clínica: a "quantidade" é o número de aplicações e o volume, não um frasco
        if conc_unidade != "mg/mL":
            return None
        vol = por_tomada_mg / conc
        ext = {1: "uma", 2: "duas"}.get(tomadas, _extenso(tomadas))
        return (f"{tomadas} ({ext}) {'aplicação' if tomadas == 1 else 'aplicações'} de ≈ {_fmt(vol)} mL "
                f"(total ≈ {_fmt(vol * tomadas)} mL), na clínica")
    if conc_unidade == "mg/mL":
        total_ml = por_tomada_mg / conc * tomadas
        return f"1 (um) frasco — volume do tratamento completo ≈ {_fmt(total_ml)} mL"
    if conc_unidade in ("mg/comprimido", "mg/cápsula"):
        unidade = "comprimido" if conc_unidade == "mg/comprimido" else "cápsula"
        if conc_unidade == "mg/cápsula":   # cápsula não se fraciona: unidades inteiras
            por_tomada = max(1, int(por_tomada_mg / conc + 0.5))
        else:
            por_tomada = round(por_tomada_mg / conc * 4) / 4
        if por_tomada <= 0:
            return None
        n = math.ceil(por_tomada * tomadas)
        return f"{n} ({_extenso(n)}) {unidade if n == 1 else unidade + 's'} de {_fmt(conc)} mg"
    return None


RADICAIS_FELINO = (
    "gat", "felin", "felis", "caracal", "lince", "leopard", "jaguar", "onca",
    "puma", "leao", "tigre", "ocelot", "pantera", "guepardo", "serval",
)
RADICAIS_CANINO = ("cao", "caes", "cachorr", "canin", "canis", "lobo")

UNIDADES_CONCENTRACAO = {
    "mg/ml": "mg/mL", "mg/comprimido": "mg/comprimido", "mg/capsula": "mg/cápsula",
}

DOSE_MAXIMA_MG_KG = 100.0   # trava de sanidade contra erro grosseiro da IA

# Faixas de dose por tomada (mg/kg) usadas SÓ para alertar, nunca para alterar a dose.
# Comece pequena e amplie com o formulário da clínica (Plumb's etc.). Confira antes de confiar.
FAIXAS_DOSE_REFERENCIA = {
    "omeprazol": (0.5, 1.0),
    "maropitant": (1.0, 2.0),
}
# Dose DIÁRIA total (mg/kg/dia) para cães, só para alertar. Carprofeno: 4,4 mg/kg/dia (2,2 mg/kg a cada 12 h
# ou 4,4 mg/kg a cada 24 h). Confira sempre o formulário/bula.
FAIXAS_DOSE_DIARIA_REFERENCIA = {"carprofeno": (4.0, 4.8)}
PESO_MAXIMO_KG = 1500.0


# --------------------------------------------------------------------------- #
# Modelos de entrada
# --------------------------------------------------------------------------- #
class ContextoClinico(BaseModel):
    """Dados do atendimento. Espécie/raça são relidos do banco quando há animal_id."""
    consulta_id: Optional[int] = None
    animal_id: Optional[int] = None
    especie: Optional[str] = Field(None, max_length=120)
    raca: Optional[str] = Field(None, max_length=120)
    idade: Optional[str] = Field(None, max_length=60)
    peso: Optional[float] = Field(None, gt=0, le=PESO_MAXIMO_KG)
    queixa_principal: Optional[str] = Field(None, max_length=4000)
    sintomas: Optional[str] = Field(None, max_length=4000)
    exame_fisico: Optional[str] = Field(None, max_length=4000)
    suspeita_diagnostica: Optional[str] = Field(None, max_length=1000)
    parecer_copiloto: Optional[str] = Field(None, max_length=12000)
    temperatura: Optional[str] = Field(None, max_length=20)
    frequencia_cardiaca: Optional[str] = Field(None, max_length=20)
    frequencia_respiratoria: Optional[str] = Field(None, max_length=20)
    tpc_segundos: Optional[str] = Field(None, max_length=20)
    mucosas: Optional[str] = Field(None, max_length=60)
    exames_anexados: Optional[str] = Field(None, max_length=1000)
    solicitar_exames_preventivos: bool = False


class ItemReceita(BaseModel):
    medicamento: str = Field(..., min_length=1, max_length=300)
    dosagem: Optional[str] = Field(None, max_length=500)
    frequencia: Optional[str] = Field(None, max_length=200)
    duracao: Optional[str] = Field(None, max_length=200)
    tipo_uso: Optional[str] = Field(None, max_length=100)
    observacoes: Optional[str] = Field(None, max_length=1000)
    recomendacoes: Optional[str] = Field(None, max_length=800)
    quantidade: Optional[str] = Field(None, max_length=200)


class MedicamentoAnalise(BaseModel):
    medicamento: str = Field(..., min_length=1, max_length=300)
    dosagem: Optional[str] = Field(None, max_length=500)
    frequencia: Optional[str] = Field(None, max_length=200)
    duracao: Optional[str] = Field(None, max_length=200)


class AnaliseMedicamentos(ContextoClinico):
    """Contexto do caso + a lista completa de medicamentos da receita (para considerar interações)."""
    medicamentos: List[MedicamentoAnalise] = Field(..., min_length=1, max_length=12)


class ReceitaSalvar(BaseModel):
    consulta_id: int
    observacoes: Optional[str] = Field(None, max_length=2000)
    itens: List[ItemReceita] = Field(..., min_length=1, max_length=20)


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _norm(texto) -> str:
    """minúsculas, sem acento, só letras/números separados por espaço."""
    if not texto:
        return ""
    sem = unicodedata.normalize("NFKD", str(texto).lower())
    sem = "".join(c for c in sem if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9/]+", " ", sem).strip()


def _grupo_animal(*descricoes) -> str:
    """felino / canino / outro, a partir de espécie, sub-espécie, raça e nome científico."""
    palavras = _norm(" ".join(str(d) for d in descricoes if d)).split()
    if any(p.startswith(RADICAIS_FELINO) for p in palavras):
        return "felino"
    if any(p.startswith(RADICAIS_CANINO) for p in palavras):
        return "canino"
    return "outro"


def _fmt(numero: float, casas: int = 2) -> str:
    """Número no padrão brasileiro, sem zeros sobrando (2,5 / 0,4 / 12).
    Abaixo de 1 usa 3 casas: com 2, uma dose de 0,015 mg/kg era exibida como '0,01' (e o cálculo usava 0,015)."""
    if casas == 2 and abs(numero) < 1:
        casas = 3
    texto = f"{numero:.{casas}f}".rstrip("0").rstrip(".")
    return texto.replace(".", ",")


def _fracao_comprimido(n: float) -> str:
    """Arredonda para 1/4 e escreve: 1/4, 1/2, 3/4, 1, 1 1/4..."""
    quartos = round(n * 4)
    inteiro, resto = divmod(quartos, 4)
    fracao = {0: "", 1: "1/4", 2: "1/2", 3: "3/4"}[resto]
    return " ".join(p for p in (str(inteiro) if inteiro else "", fracao) if p) or "0"


def _numero(valor) -> Optional[float]:
    """Converte para float positivo; devolve None se inválido."""
    try:
        n = float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _frequencia_texto(horas) -> str:
    h = _numero(horas)
    if h is None or h > 168:
        return "Conforme orientação do veterinário"
    h = int(round(h))
    return FREQUENCIA_ROTULO.get(h, f"A cada {h} horas")


def _duracao_texto(dias) -> str:
    d = _numero(dias)
    if d is None or d > 120:
        return "Conforme orientação do veterinário"
    d = int(round(d))
    return "Por 1 dia" if d == 1 else f"Por {d} dias"


def _calcular_dosagem(dose_mg_kg, peso, conc_valor, conc_unidade, alertas, nome):
    """
    Monta o texto da dosagem. Toda a aritmética é feita aqui.
    Ex.: '25 mg/kg (625 mg por tomada ≈ 1,25 mL)'.
    """
    if peso is None:
        alertas.append(
            f"{nome}: peso não informado, a dose total não foi calculada. "
            "Informe o peso do paciente e gere a receita de novo."
        )
        return f"{_fmt(dose_mg_kg)} mg/kg (calcular com o peso do paciente)"

    total_mg = dose_mg_kg * peso
    texto = f"{_fmt(dose_mg_kg)} mg/kg ({_fmt(total_mg)} mg por tomada"

    conc = _numero(conc_valor)
    if conc and conc_unidade == "mg/mL":
        texto += f" ≈ {_fmt(total_mg / conc)} mL"
    elif conc and conc_unidade in ("mg/comprimido", "mg/cápsula"):
        unidade = "comprimido" if conc_unidade == "mg/comprimido" else "cápsula"
        n = total_mg / conc
        if conc_unidade == "mg/cápsula":   # cápsula não se fraciona: unidades inteiras
            n_cap = max(1, int(n + 0.5))
            dado = n_cap * conc
            texto += f" ≈ {n_cap} cápsula{'s' if n_cap > 1 else ''} de {_fmt(conc)} mg"
            if abs(dado - total_mg) / total_mg > 0.20:
                alertas.append(
                    f"{nome}: a cápsula não se fraciona; {n_cap} cápsula(s) = {_fmt(dado)} mg, diferente da dose "
                    f"calculada ({_fmt(total_mg)} mg) em mais de 20%. Avalie outra apresentação ou manipulação."
                )
            return texto + ")"
        if round(n * 4) == 0:
            texto += f" < 1/4 de {unidade} de {_fmt(conc)} mg"
            alertas.append(
                f"{nome}: dose menor que 1/4 de {unidade}. Considere outra apresentação "
                "ou manipulação."
            )
        else:
            dado = round(n * 4) / 4 * conc
            texto += f" ≈ {_fracao_comprimido(n)} de {unidade} de {_fmt(conc)} mg"
            if abs(dado - total_mg) / total_mg > 0.20:
                alertas.append(
                    f"{nome}: o fracionamento ({_fmt(dado)} mg) difere mais de 20% da dose "
                    f"calculada ({_fmt(total_mg)} mg). Avalie outra apresentação ou manipulação."
                )
    return texto + ")"


def _concentracao_da_apresentacao(apresentacao: str):
    """Quando a IA não preenche concentracao_valor/unidade, tenta ler da apresentação
    ('Cápsula 20 mg', 'comprimido 1,5 mg', 'solução 0,3 mg/mL'). Devolve (valor, unidade) ou None."""
    # O número é lido do texto original: _norm troca a vírgula decimal por espaço ('1,5' viraria '15').
    t = _norm(apresentacao)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*mg(?:\s*/\s*(ml|g|comprimido|capsula|cápsula))?",
                  str(apresentacao or "").lower())
    if not m:
        return None
    valor = m.group(1).replace(",", ".")
    sufixo = _norm(m.group(2)) if m.group(2) else None
    if sufixo == "g":                       # pomadas/géis (mg/g) não entram no cálculo de dose
        return None
    if sufixo == "ml":
        return valor, "mg/mL"
    if sufixo in ("comprimido", "capsula"):
        return valor, UNIDADES_CONCENTRACAO[f"mg/{sufixo}"]
    if re.search(r"\bcaps(ula|ulas)?\b", t):
        return valor, "mg/cápsula"
    if re.search(r"\b(comprimido|comprimidos|comp)\b", t):
        return valor, "mg/comprimido"
    if re.search(r"\b(solucao|suspensao|injecao|injetavel|gotas|xarope)\b", t):
        return None                         # líquido sem 'mg/mL' explícito: ambíguo, não adivinhar
    return None


def _roedor_ou_lagomorfo(*descricoes) -> bool:
    palavras = _norm(" ".join(str(d) for d in descricoes if d)).split()
    return any(p.startswith(RADICAIS_LAGOMORFO_ROEDOR) for p in palavras)


def _carregar_contexto(db: Session, ctx: ContextoClinico) -> dict:
    """Junta o que veio da tela com o cadastro do animal (o cadastro manda na espécie/raça)."""
    dados = ctx.model_dump()
    animal = db.query(Animal).filter(Animal.id == ctx.animal_id).first() if ctx.animal_id else None
    if ctx.animal_id and not animal:
        raise HTTPException(status_code=404, detail="Paciente (animal) não encontrado.")

    dados.update({"nome_cientifico": None, "sub_especie": None, "porte": None,
                  "sexo": None, "castrado": None})
    if animal:
        for campo in ("especie", "sub_especie", "raca", "porte", "sexo", "castrado", "nome_cientifico"):
            valor = getattr(animal, campo, None)
            if valor not in (None, ""):
                dados[campo] = valor
        if dados.get("peso") is None:
            dados["peso"] = _numero(getattr(animal, "peso", None))
        if not dados.get("idade") and getattr(animal, "idade", None) not in (None, ""):
            dados["idade"] = str(animal.idade)

    dados["grupo"] = _grupo_animal(
        dados.get("especie"), dados.get("sub_especie"), dados.get("raca"), dados.get("nome_cientifico")
    )
    return dados


def _exigir_parecer(d: dict) -> None:
    if not (d.get("suspeita_diagnostica") or d.get("parecer_copiloto")):
        raise HTTPException(
            status_code=422,
            detail="Execute a análise do Copiloto Clínico antes: a sugestão parte do parecer da IA.",
        )


def _texto_do_caso(d: dict) -> str:
    """Bloco de texto com o caso, igual para receita e exames."""
    vitais = (
        f"Temp={d.get('temperatura') or 'não aferida'} °C; FC={d.get('frequencia_cardiaca') or 'não aferida'} bpm; "
        f"FR={d.get('frequencia_respiratoria') or 'não aferida'} ir/min; "
        f"TPC (tempo de preenchimento capilar)={d.get('tpc_segundos') or 'não aferido'} s; Mucosas={d.get('mucosas') or 'não informadas'}"
    )
    peso = f"{_fmt(d['peso'])} kg" if d.get("peso") else "NÃO INFORMADO"
    return (
        f"- Espécie: {d.get('especie') or 'não informada'} | Nome científico: {d.get('nome_cientifico') or 'não informado'}"
        f" | Raça: {d.get('raca') or 'SRD'} | Porte: {d.get('porte') or 'não informado'}\n"
        f"- Sexo: {d.get('sexo') or 'não informado'} | Castrado: {d.get('castrado') or 'não informado'}"
        f" | Idade: {d.get('idade') or 'não informada'} | Peso: {peso}\n"
        f"- Queixa principal: {d.get('queixa_principal') or '-'}\n"
        f"- Sintomas: {d.get('sintomas') or '-'}\n"
        f"- Exame físico: {d.get('exame_fisico') or '-'}\n"
        f"- Sinais vitais: {vitais}\n"
        f"- Exames já anexados: {d.get('exames_anexados') or 'nenhum'}\n"
        f"- Suspeita diagnóstica: {d.get('suspeita_diagnostica') or '-'}\n"
        f"- Parecer do Copiloto Clínico:\n{d.get('parecer_copiloto') or '-'}"
    )


def _erro_ia(exc: IAIndisponivelError):
    logger.warning("IA indisponível no atendimento: %s | %s", exc, getattr(exc, "tentativas", None))
    raise HTTPException(
        status_code=503,
        detail="A IA está indisponível no momento. Tente novamente em instantes "
               "ou verifique as Configurações de IA.",
    )


# --------------------------------------------------------------------------- #
# Receita
# --------------------------------------------------------------------------- #
_VIAS_NAO_ORAIS = ("subcut", "intraven", "intramus", "injet", "topic", "oftalm", "otolog",
                   "cutane", "transdermic", "retal", "inalat")
_VIAS_NAO_ORAIS_SIGLAS = {"sc", "sq", "iv", "im", "io"}
_VIAS_TOPICAS = ("topic", "oftalm", "otolog", "cutane", "transdermic", "inalat")


def _via_oral(via: str) -> bool:
    """Na dúvida (via vazia ou 'a definir') trata como oral: é o lado seguro."""
    tokens = _norm(via).split()
    return not any(t.startswith(_VIAS_NAO_ORAIS) or t in _VIAS_NAO_ORAIS_SIGLAS for t in tokens)


def _via_sistemica(via: str) -> bool:
    return not any(t.startswith(_VIAS_TOPICAS) for t in _norm(via).split())


def _validar_estrutura_receita(dados: dict) -> dict:
    if not isinstance(dados.get("medicamentos"), list):
        raise ValueError("JSON sem a lista 'medicamentos'.")
    return dados


def _montar_receita(dados: dict, peso: Optional[float], grupo: str,
                    sensivel_oral: bool = False, texto_caso: str = "") -> dict:
    """Valida cada medicamento sugerido, calcula a dose e aplica as travas de segurança."""
    itens, alertas = [], []
    vistos = set()
    proibidos = PROIBIDOS_POR_GRUPO.get(grupo, PROIBIDOS_POR_GRUPO["outro"])
    caso_n = _norm(texto_caso)
    caso_contraindica_aine = any(m in caso_n for m in MARCADORES_CONTRAINDICACAO_AINE)
    meta = []  # (nome normalizado, via sistêmica?) em paralelo a `itens`

    for bruto in dados["medicamentos"][:12]:
        if not isinstance(bruto, dict):
            continue
        nome = str(bruto.get("nome") or "").strip()[:150]
        if not nome:
            continue
        nome_n = _norm(nome)

        if any(p in nome_n for p in proibidos):
            alertas.append(
                f"{nome}: removido por segurança (risco conhecido para esta espécie). "
                "O veterinário pode incluir manualmente, se julgar adequado."
            )
            continue
        via_bruta = str(bruto.get("via") or "")
        motivo_oral = next((m for p_, m in ORAL_INEFICAZ_POR_GRUPO.get(grupo, {}).items() if p_ in nome_n), None)
        if motivo_oral and _via_oral(via_bruta):
            alertas.append(
                f"{nome}: item oral removido por segurança ({motivo_oral}). Indique aplicação parenteral "
                "(SC/IM/IV) na clínica ou outro analgésico; o veterinário pode incluir manualmente, se julgar adequado."
            )
            continue
        if sensivel_oral and _via_oral(via_bruta) \
                and any(p in nome_n for p in ANTIBIOTICOS_RISCO_ORAL_ROEDORES):
            alertas.append(
                f"{nome}: removido por segurança (penicilina, lincosamida e cefalosporina por via oral "
                "causam enterotoxemia em coelhos, roedores e cobaias). "
                "O veterinário pode incluir manualmente, se julgar adequado."
            )
            continue
        sistemica = _via_sistemica(via_bruta)
        if sistemica and caso_contraindica_aine and any(p in nome_n for p in AINES):
            alertas.append(
                f"{nome}: AINE removido por segurança (o caso menciona intoxicação, desidratação grave "
                "ou comprometimento renal/hepático). O veterinário pode incluir manualmente, se julgar adequado."
            )
            continue
        if nome_n in vistos:
            continue
        vistos.add(nome_n)

        apresentacao = str(bruto.get("apresentacao") or "").strip()[:150]
        titulo = f"{nome} {apresentacao}".strip()

        conc_unidade = UNIDADES_CONCENTRACAO.get(_norm(bruto.get("concentracao_unidade")))
        conc_valor = bruto.get("concentracao_valor")
        if not (_numero(conc_valor) and conc_unidade):
            achada = _concentracao_da_apresentacao(apresentacao)
            if achada:
                conc_valor, conc_unidade = achada
        dose = _numero(bruto.get("dose_mg_kg"))
        posologia = str(bruto.get("posologia_texto") or "").strip()[:300]

        if dose is not None and dose > DOSE_MAXIMA_MG_KG:
            alertas.append(f"{nome}: dose de {_fmt(dose)} mg/kg fora do plausível, item descartado.")
            continue
        faixa = next((f for k, f in FAIXAS_DOSE_REFERENCIA.items() if k in nome_n), None)
        if dose is not None and faixa and grupo in ("canino", "felino") and not faixa[0] <= dose <= faixa[1]:
            alertas.append(
                f"{nome}: dose de {_fmt(dose)} mg/kg fora da faixa de referência cadastrada "
                f"({_fmt(faixa[0])} a {_fmt(faixa[1])} mg/kg). Confira no formulário antes de emitir."
            )
        faixa_dia = next((f for k, f in FAIXAS_DOSE_DIARIA_REFERENCIA.items() if k in nome_n), None)
        freq_h = _numero(bruto.get("frequencia_horas"))
        if dose is not None and faixa_dia and freq_h and grupo == "canino":
            diaria = dose * 24 / freq_h
            if not faixa_dia[0] <= diaria <= faixa_dia[1]:
                alertas.append(
                    f"{nome}: a dose diária resultante é de {_fmt(diaria)} mg/kg/dia, fora da faixa de referência "
                    f"cadastrada ({_fmt(faixa_dia[0])} a {_fmt(faixa_dia[1])} mg/kg/dia). Confira dose e frequência no formulário."
                )
        if dose is not None:
            dosagem = _calcular_dosagem(dose, peso, conc_valor,
                                        conc_unidade, alertas, nome)
        elif posologia:
            dosagem = posologia
        else:
            dosagem = "Definir a dose com o veterinário"
            alertas.append(f"{nome}: a IA não indicou dose segura. Defina a dose manualmente.")

        tipo = _norm(bruto.get("disponibilidade")).upper().replace(" ", "_")
        if tipo not in TIPO_USO_ROTULO:
            tipo = "A_CONFIRMAR"

        dias_n = _numero(bruto.get("duracao_dias"))
        conc_n = _numero(conc_valor)
        if (dose is not None and peso is not None and conc_n and conc_unidade == "mg/mL"
                and grupo in ("canino", "felino")):
            volume = dose * peso / conc_n
            limite = LIMITE_VOLUME_ORAL_ML if _via_oral(via_bruta) else LIMITE_VOLUME_INJETAVEL_ML
            if volume > limite:
                alertas.append(
                    f"{nome}: volume de ≈ {_fmt(volume)} mL por tomada parece alto para esta espécie. Confira a "
                    "concentração do produto ou escolha outra apresentação (ex.: comprimido ou concentração maior)."
                )
        if tipo == "CLINICA" and dias_n and dias_n > MAX_DIAS_CLINICA:
            alertas.append(
                f"{nome}: aplicação na clínica por {int(round(dias_n))} dias. Confirme se haverá retornos diários "
                "ou prefira apresentação oral para uso em casa."
            )

        via = str(bruto.get("via") or "").strip().lower()[:40]
        obs = str(bruto.get("observacao") or "").strip()[:400]
        observacoes = " ".join(p for p in (f"Via: {via}." if via else "", obs) if p)

        recomendacoes = corrigir_siglas(str(bruto.get("recomendacoes") or "").strip()[:300])
        lista_controle = _lista_controle_valida(bruto.get("lista_controle"))
        lista_ref = next((l for k, l in LISTA_CONHECIDA.items() if k in nome_n), None)
        if lista_ref and lista_controle != lista_ref:
            if lista_controle:
                alertas.append(
                    f"{nome}: a IA indicou a lista {lista_controle}, mas este princípio ativo consta na lista "
                    f"{lista_ref} da Portaria 344/98. Foi usada a lista {lista_ref}; confirme na norma vigente."
                )
            lista_controle = lista_ref
        # A IA classifica; a lista fixa é só uma rede de segurança contra falso negativo.
        controlado = (_verdadeiro(bruto.get("controlado")) or bool(lista_controle)
                      or any(c in nome_n for c in POSSIVELMENTE_CONTROLADOS))
        quantidade = None
        if controlado:
            alertas.append(
                f"{nome}: medicamento de controle especial"
                f"{f' (lista {lista_controle})' if lista_controle else ''} (Portaria SVS/MS 344/98). "
                "Confira a lista vigente, o tipo de receituário exigido (A1-A3: Notificação de Receita A; "
                "B1-B2: Notificação de Receita B; C1: receita de controle especial) e a quantidade antes de emitir."
            )
            if "manipulad" in _norm(apresentacao):
                alertas.append(
                    f"{nome}: apresentação manipulada de medicamento controlado. Confirme com a farmácia de "
                    "manipulação e use apenas o receituário correto."
                )
            quantidade = _quantidade_total(dose, peso, conc_valor, conc_unidade,
                                           bruto.get("frequencia_horas"), bruto.get("duracao_dias"),
                                           clinica=(tipo == "CLINICA"))
            if tipo == "CLINICA":
                alertas.append(
                    f"{nome}: item de aplicação na clínica; a quantidade indicada corresponde às aplicações "
                    "previstas, não a um frasco dispensado ao tutor. Confirme."
                )
            if dias_n and dias_n > MAX_DIAS_CONTROLADO:
                alertas.append(
                    f"{nome}: medicamento controlado por {int(round(dias_n))} dias. Confirme a necessidade e "
                    "programe reavaliação."
                )

        itens.append({
            "medicamento": titulo,
            "dosagem": dosagem,
            "frequencia": _frequencia_texto(bruto.get("frequencia_horas")),
            "duracao": _duracao_texto(bruto.get("duracao_dias")),
            "tipo_uso": tipo,
            "tipo_uso_rotulo": TIPO_USO_ROTULO[tipo],
            "observacoes": observacoes,
            "controlado": controlado,
            "lista_controle": lista_controle,
            "recomendacoes": recomendacoes,
            "quantidade": quantidade,
        })
        meta.append((nome_n, sistemica))

    antimicrobianos = [it["medicamento"] for it in itens
                       if any(a in _norm(it["medicamento"]) for a in ANTIMICROBIANOS)]
    if len(antimicrobianos) > 1:
        alertas.append(
            "Mais de um antimicrobiano na receita (" + "; ".join(antimicrobianos) + "). Confirme se a associação "
            "é intencional; em geral inicia-se com um só e ajusta-se pelo antibiograma."
        )

    # AINE + corticosteroide na mesma receita: risco de úlcera. Remove os dois e deixa a escolha ao veterinário.
    idx_aine = {i for i, (n, s) in enumerate(meta) if s and any(a in n for a in AINES)}
    idx_cort = {i for i, (n, s) in enumerate(meta) if s and any(c in n for c in CORTICOIDES)}
    if idx_aine and idx_cort:
        remover = idx_aine | idx_cort
        nomes = ", ".join(itens[i]["medicamento"].split(" ")[0] for i in sorted(remover))
        itens = [it for i, it in enumerate(itens) if i not in remover]
        alertas.append(
            f"AINE e corticosteroide não podem ser prescritos juntos (risco de úlcera gastrointestinal). "
            f"Itens removidos: {nomes}. Escolha um deles e inclua manualmente."
        )

    for aviso in dados.get("alertas") or []:
        if isinstance(aviso, str) and aviso.strip():
            n_av = _norm(aviso)
            # Cães e gatos: sem alerta genérico de notificação (só a barragem trata raiva etc.).
            if (grupo in ("canino", "felino") and "notific" in n_av and "raiva" not in n_av
                    and any(k in n_av for k in ("obrigatori", "oficial", "mapa", "imediat"))):
                continue
            alertas.append(corrigir_siglas(aviso.strip()[:300]))
    return {"itens": itens, "alertas": alertas[:15]}


def _doenca_oficial_do_caso(d: dict, db: Session) -> Optional[dict]:
    """Barragem sanitária: a doença oficial do caso (ou None). Usada pelas rotas que NÃO
    podem consultar o LLM quando há doença de notificação/controle oficial."""
    return doenca_do_parecer(d.get("parecer_copiloto"), d.get("especie"), db=db) or \
        detectar_doenca_oficial(d.get("suspeita_diagnostica") or "", d.get("especie"), db=db)


def _alertas_receita_oficial(doenca: dict) -> List[str]:
    alertas = [
        f"Suspeita de doença de notificação obrigatória: {doenca['nome']}.",
        doenca.get("tratamento") or "Não há tratamento medicamentoso autorizado para esta condição: não prescrever fármacos "
        "(antimicrobianos, AINEs, corticosteroides ou medicamentos humanos).",
        "Isolar imediatamente os animais suspeitos e restringir o trânsito até a orientação oficial.",
        f"Notificar imediatamente: {doenca.get('orgao') or 'serviço veterinário oficial (MAPA / órgão estadual de defesa sanitária)'}.",
        "A destinação dos animais (ex.: sacrifício sanitário) é definida e conduzida exclusivamente "
        "pelo serviço oficial após a confirmação laboratorial.",
    ]
    if doenca.get("aviso"):
        alertas.append(doenca["aviso"])
    return alertas


def _resposta_barragem_receita(doenca: dict, peso: Optional[float]) -> dict:
    return {
        "itens": [],
        "alertas": _alertas_receita_oficial(doenca),
        "peso_usado": peso,
        "provedor": "barragem_sanitaria",
        "modelo": None,
        "doenca_oficial": doenca["nome"],
        "aviso": f"Suspeita de {doenca['nome']}: doença de notificação oficial. Nenhum medicamento "
                 "é sugerido; o veterinário pode adicionar itens manualmente, sob sua responsabilidade.",
    }


@router.post("/sugerir-receita")
def sugerir_receita(
    ctx: ContextoClinico,
    usuario_logado=Depends(exigir_perfil(PERFIS_CLINICOS)),
    db: Session = Depends(get_db),
):
    d = _carregar_contexto(db, ctx)
    _exigir_parecer(d)

    # Barragem sanitária: doença oficial NÃO passa pelo LLM. Receita vazia + alerta fixo.
    doenca = _doenca_oficial_do_caso(d, db)
    if doenca:
        logger.info("sugerir-receita: barragem aplicada (%s), LLM não consultado", doenca["nome"])
        return _resposta_barragem_receita(doenca, d.get("peso"))

    prompt = (
        "Você é um médico veterinário clínico experiente no Brasil, apoiando a elaboração de uma "
        "receita. Com base SOMENTE no caso abaixo, sugira o tratamento medicamentoso necessário.\n\n"
        f"CASO:\n{_texto_do_caso(d)}\n\n"
        "REGRAS OBRIGATÓRIAS:\n"
        "1. Sugira os medicamentos justificados pelo parecer clínico para um suporte terapêutico completo e seguro, sem omitir itens essenciais (ex: analgésicos), SALVO nas exceções de segurança (2.3 e regra 7), que PREVALECEM sobre esta regra. Se nenhum medicamento for justificado, devolva a lista vazia. Protetor gástrico só se o parecer descrever AINE, corticoide, vômito, gastrite ou risco gastrointestinal.\n"
        "2. ESPÉCIE E TOXICOLOGIA VETERINÁRIA: Doses, segurança e antídotos dependem OBRIGATORIAMENTE da ESPÉCIE. "
        "É expressamente proibido sugerir tratamentos humanos tóxicos para animais (ex.: paracetamol/permetrina/azul de metileno para felinos; ibuprofeno/diclofenaco para cães). Para intoxicação por Paracetamol, o antídoto padrão é a Acetilcisteína.\n"
        "2.1 TOXICOLOGIA DE LAGOMORFOS E ROEDORES: É TERMINANTEMENTE PROIBIDO prescrever penicilinas (ex: Amoxicilina), lincosamidas ou cefalosporinas por via ORAL para coelhos, porquinhos-da-índia e chinchilas. Estas substâncias causam enterotoxemia e morte nestas espécies. Se uma destas drogas tiver sido prescrita acidentalmente ou administrada pelo tutor, OBRIGATORIAMENTE gere um alerta sobre a necessidade de estabilização do choque e disbiose cecal.\n"
        "2.2 ANIMAIS DE PRODUÇÃO E AQUICULTURA: Para espécies de produção (corte/consumo humano), é OBRIGATORIAMENTE NECESSÁRIO informar no campo de "
        "alertas ou recomendações o período de carência (retirada) regulamentado pelos órgãos competentes para o fármaco prescrito, garantindo a segurança alimentar.\n"
        "2.3 DOENÇA DE NOTIFICAÇÃO OFICIAL: Se o caso sugerir doença de notificação obrigatória ou de controle oficial (ex.: raiva, febre aftosa, mormo, influenza aviária, Newcastle, peste suína; em cães e gatos, só doenças de notificação oficial para essas espécies, como a raiva), NÃO prescreva nenhum medicamento: devolva \"medicamentos\": [] e, em \"alertas\", apenas a orientação de isolar o animal e notificar o serviço veterinário oficial. Em cães e gatos NÃO escreva alerta de notificação obrigatória, exceto para raiva.\n"
        "3. NÃO invente dose. Para espécie exótica/silvestre sem dose bem estabelecida na "
        "literatura, deixe dose_mg_kg como null e escreva em posologia_texto "
        "\"definir dose com o veterinário\".\n"
        "4. Informe a dose em mg/kg POR TOMADA (dose_mg_kg) e a concentração do produto "
        "comercial (concentracao_valor + concentracao_unidade). NÃO calcule mg totais, mL nem frações: o sistema Python fará essa matemática. Para uso "
        "tópico, oftálmico ou sem dose em mg/kg, deixe dose_mg_kg null e descreva em posologia_texto.\n"
        "5. frequencia_horas é o intervalo em horas (24, 12, 8, 6...). duracao_dias é um número.\n"
        "6. disponibilidade: \"HUMANO\" (comum em farmácia/drogaria), \"VETERINARIO\" (vendido em "
        "pet shop, agropecuária ou clínica veterinária) ou \"CLINICA\" (injetável aplicado na clínica).\n"
        "7. PROPORCIONALIDADE E INTERAÇÕES FATAIS: A prescrição deve ser compatível com a gravidade. Evite opioides fortes em quadros leves; quando indicados, pelo menor tempo necessário (poucos dias, com reavaliação). "
        "É EXPRESSAMENTE PROIBIDO prescrever AINEs e Corticosteroides simultaneamente (risco severo de úlcera). NUNCA prescreva AINEs para pacientes com suspeita de intoxicação aguda, desidratação severa ou falência renal/hepática.\n"
        "8. MEDICINA BASEADA EM EVIDÊNCIAS: Mantenha o foco estritamente na medicina alopática veterinária. É proibido prescrever ou sugerir chás, simpatias, homeopatia não comprovada ou remédios caseiros humanos.\n"
        "9. controlado: true se o princípio ativo consta nas listas de controle especial da Portaria "
        "SVS/MS 344/98 da ANVISA (listas A1 a C5) OU nas normativas de controle especial do MAPA "
        "(ex: IN 35/2017 para anestésicos). Nesse caso informe a lista em lista_controle (ex: 'C1', 'MAPA'). Caso contrário, false e null.\n"
        "10. recomendacoes: orientações práticas de administração para ESTE paciente. Máximo de 250 caracteres, em português.\n"
        "11. PROIBIDO INVENTAR CONCENTRAÇÕES E DOSES (MATEMÁTICA PREGUIÇOSA): É terminantemente proibido criar apresentações fictícias (ex: \"1 mg/comprimido\" para Maropitant) apenas para contornar a matemática. Use EXATAMENTE os miligramas reais de prateleira da indústria (ex: 16 mg, 20 mg). O sistema Python fará o cálculo do fracionamento. Se incompatível com o peso, prescreva como Fórmula Manipulada, EXCETO medicamento de controle especial: nele use a apresentação comercial real ou deixe a dose para o veterinário. Se for uma ESPÉCIE EXÓTICA e a dose da medicação escolhida (ex: Enrofloxacino, Metoclopramida, Meloxicam) precisar ser confirmada, escreva na via 'A definir' e deixe a dose_mg_kg como null. Não adivinhe a quantidade em ml.\n"
        "12. É EXPRESSAMENTE PROIBIDO INVENTAR CONCENTRAÇÕES DE PRATELEIRA: Use as concentrações reais da indústria brasileira aprovadas pelos órgãos competentes. Se a IA decidir transformar um medicamento que só existe em comprimido (como Maropitant ou Famotidina) numa suspensão oral ou xarope, deve OBRIGATORIAMENTE escrever 'Fórmula Manipulada' no nome da apresentação.\n"
        "13. PRIORIDADE PARA VIA ORAL: Como o tratamento é domiciliar, priorize SEMPRE apresentações para uso oral (comprimidos, cápsulas, gotas, suspensão). NÃO prescreva formulações injetáveis para uso em casa. Se for injetável, que seja estritamente para disponibilidade \"CLINICA\". EXCEÇÃO: fármaco sem eficácia por via oral na espécie (ex.: buprenorfina em cães) deve ser indicado como injetável (SC/IM/IV) para aplicação na clínica, com disponibilidade \"CLINICA\". Aplicação na clínica NÃO serve para uso domiciliar contínuo: limite-a à fase aguda (poucos dias) e use via oral no restante. Em cães e gatos acima de 15 kg prefira comprimidos; líquidos só para animais pequenos ou doses que não caibam em comprimido, com volume plausível por tomada (oral até cerca de 10 mL; injetável até cerca de 5 mL por local).\n"
        "14. ALERTAS DE SEGURANÇA (MEDICINA DEFENSIVA): Seja cauteloso com a alta. Inclua no array 'alertas' ou nas 'recomendacoes' instruções rigorosas para o tutor: monitorar sinais de hipersensibilidade, proibir a automedicação e orientar retorno imediato à clínica em caso de piora. Se indicar antibióticos, respeite a janela terapêutica mínima (nunca 2 ou 3 dias). Indique UM antimicrobiano por vez: antibiótico de segunda linha citado no parecer \"se houver piora\" NÃO entra na receita inicial.\n"
        "15. SILÊNCIO ESTRUTURAL (PROIBIDO META-COMENTÁRIOS): É expressamente proibido justificar suas escolhas, narrar suas ações ou conversar com o sistema dentro dos valores do JSON. Não escreva justificativas como 'O fracionamento será realizado pelo sistema', 'A IA decidiu' ou 'Conforme a regra'. Os campos de texto devem conter APENAS orientações clínicas diretas e pragmáticas para o tutor, redigidas no jargão veterinário brasileiro (nunca traduza literalmente do inglês, ex: use 'claudicação' e não 'lamença').\n"
        "16. FIDELIDADE AO PARECER: a receita executa o parecer clínico, não o reescreve. Use SOMENTE fármacos citados no parecer, com a mesma dose (mg/kg) e a mesma frequência. Mantenha a via do parecer; só altere a via quando a regra 7, 13 ou 2.1 exigir (injetável para uso domiciliar vira disponibilidade \"CLINICA\"; fármaco ineficaz por via oral na espécie continua injetável) e, nesse caso, mantenha o MESMO fármaco. Todo analgésico ou anti-inflamatório citado no parecer deve constar na receita; se algum for omitido por segurança (regra 7), registre o motivo em 'alertas'. Se o parecer citar via ou dose incoerente para a espécie, escolha a via correta e descreva o ajuste em 'alertas' em uma frase clínica.\n"
        "17. Não escreva nada fora do JSON.\n\n"
        "FORMATO (JSON):\n"
        "{\"medicamentos\": [{\"nome\": \"Dipirona sódica\", \"apresentacao\": \"gotas 500 mg/mL\", "
        "\"concentracao_valor\": 500, \"concentracao_unidade\": \"mg/mL\", \"dose_mg_kg\": 25, "
        "\"posologia_texto\": null, \"via\": \"oral\", \"frequencia_horas\": 8, \"duracao_dias\": 5, "
        "\"disponibilidade\": \"HUMANO\", \"controlado\": false, \"lista_controle\": null, \"recomendacoes\": \"Administrar junto com o alimento.\", \"observacao\": \"Indicado para analgesia.\"}], "
        "\"alertas\": [\"cuidados gerais, interações ou monitoramento\"]}"
    )
    try:
        resp = gerar_json(db, prompt, validar=_validar_estrutura_receita)
    except IAIndisponivelError as exc:
        _erro_ia(exc)

    receita = _montar_receita(
        resp.dados, d.get("peso"), d["grupo"],
        sensivel_oral=_roedor_ou_lagomorfo(d.get("especie"), d.get("sub_especie"), d.get("raca"),
                                           d.get("nome_cientifico")),
        texto_caso=_texto_do_caso(d),
    )

    # Segunda barragem, agora sobre a SAÍDA do LLM: se ele mesmo apontou doença oficial que a
    # detecção do parecer não pegou, a receita é descartada e vale o alerta fixo.
    texto_saida = " ".join(
        [str(i.get(k) or "") for i in receita["itens"] for k in ("medicamento", "observacoes", "recomendacoes")]
        + [a for a in (resp.dados.get("alertas") or []) if isinstance(a, str)]
    ).strip()
    doenca_saida = detectar_doenca_oficial(texto_saida, d.get("especie"), db=db) if texto_saida else None
    if doenca_saida:
        logger.warning("sugerir-receita: barragem aplicada na SAÍDA do LLM (%s)", doenca_saida["nome"])
        return _resposta_barragem_receita(doenca_saida, d.get("peso"))

    if d.get("peso") is None:
        receita["alertas"].insert(0, "Peso não informado: as doses não foram calculadas.")
    receita.update({
        "peso_usado": d.get("peso"),
        "provedor": resp.provedor,
        "modelo": resp.modelo,
        "aviso": "Sugestão gerada por IA. Doses, apresentações e disponibilidade precisam ser "
                 "conferidas pelo veterinário responsável antes da emissão.",
    })
    return receita


def _validar_analise(dados: dict) -> dict:
    if not isinstance(dados.get("medicamentos"), list):
        raise ValueError("JSON sem a lista 'medicamentos'.")
    return dados


@router.post("/analisar-medicamentos")
def analisar_medicamentos(
    corpo: AnaliseMedicamentos,
    usuario_logado=Depends(exigir_perfil(PERFIS_CLINICOS)),
    db: Session = Depends(get_db),
):
    """
    Para medicamentos incluídos/editados à mão: a IA classifica o controle especial e escreve
    as recomendações de administração considerando o paciente e os demais itens da receita.
    """
    d = _carregar_contexto(db, corpo)

    # Barragem sanitária: em doença oficial não há análise de receita pelo LLM. Quem adicionou o
    # medicamento à mão recebe o alerta fixo; o controle especial segue só a lista fixa (sem LLM).
    doenca = _doenca_oficial_do_caso(d, db)
    if doenca:
        logger.info("analisar-medicamentos: barragem aplicada (%s), LLM não consultado", doenca["nome"])
        alerta = (f"Prescrição não recomendada: suspeita de {doenca['nome']}, doença de notificação "
                  "oficial sem tratamento medicamentoso autorizado. Notifique o serviço veterinário oficial.")
        saida = []
        for m in corpo.medicamentos:
            nome_n = _norm(m.medicamento)
            saida.append({
                "medicamento": m.medicamento,
                "controlado": any(c in nome_n for c in POSSIVELMENTE_CONTROLADOS),
                "lista_controle": None,
                "recomendacoes": alerta,
            })
        return {
            "itens": saida,
            "alertas": _alertas_receita_oficial(doenca),
            "provedor": "barragem_sanitaria",
            "modelo": None,
            "doenca_oficial": doenca["nome"],
        }

    linhas = "\n".join(
        f"{i + 1}. {m.medicamento}"
        f" | dosagem: {m.dosagem or '-'} | frequência: {m.frequencia or '-'} | duração: {m.duracao or '-'}"
        for i, m in enumerate(corpo.medicamentos)
    )
    prompt = (
        "Você é um médico veterinário clínico experiente no Brasil, revisando uma receita.\n\n"
        f"PACIENTE E CASO:\n{_texto_do_caso(d)}\n\n"
        f"MEDICAMENTOS DA RECEITA:\n{linhas}\n\n"
        "REGRAS OBRIGATÓRIAS PARA CADA MEDICAMENTO (na MESMA ORDEM):\n"
        "1. controlado: true se o princípio ativo consta nas listas de controle especial da Portaria "
        "SVS/MS 344/98 da ANVISA (listas A1 a C5) OU nas normativas de controle especial do MAPA "
        "(ex: IN 35/2017 para anestésicos veterinários). Nesse caso, informe a lista correspondente "
        "em lista_controle (ex: 'C1', 'MAPA'); caso contrário, false e null.\n"
        "2. recomendacoes: orientações práticas de administração para ESTE paciente (espécie, peso e idade): "
        "relação com alimentação ou jejum, horário, intervalo em relação aos OUTROS medicamentos desta "
        "receita, interações e sinais para suspender e procurar a clínica.\n"
        "3. SILÊNCIO ESTRUTURAL E MEDICINA VETERINÁRIA: Escreva APENAS a orientação clínica direta e imperativa "
        "para o tutor ler na receita impressa (ex: 'Dar de estômago vazio', 'Suspender em caso de vômito'). "
        "PROIBIDO meta-comentários. Baseie-se estritamente na farmacologia veterinária da ESPÉCIE do paciente. "
        "Máximo de 250 caracteres, em português, sem repetir dose nem frequência. Se não houver nada relevante, use null.\n"
        "4. ALERTA DE INTERAÇÕES GRAVES: Se o veterinário prescreveu AINEs e Corticosteroides na mesma receita, "
        "ou se prescreveu medicamentos humanos perigosos para a espécie (ex: paracetamol para gatos), USE o campo "
        "'recomendacoes' para alertar o tutor a confirmar a prescrição com a clínica imediatamente, devido ao alto risco.\n"
        "5. MEDICINA BASEADA EM EVIDÊNCIAS: Se o usuário inseriu terapias não convencionais (chás, homeopatia), "
        "apenas deixe a recomendacao como null. Não endosse nem crie posologias para tratamentos sem base científica.\n"
        "6. Não escreva nada fora do JSON.\n\n"
        "FORMATO (JSON):\n"
        "{\"medicamentos\": [{\"nome\": \"Sucralfato\", \"controlado\": false, \"lista_controle\": null, "
        "\"recomendacoes\": \"Dar de estômago vazio. Manter intervalo de pelo menos 2 horas em relação aos outros medicamentos.\"}]}"
    )
    try:
        resp = gerar_json(db, prompt, validar=_validar_analise)
    except IAIndisponivelError as exc:
        _erro_ia(exc)

    brutos = [b for b in resp.dados["medicamentos"] if isinstance(b, dict)]
    por_nome = {_norm(b.get("nome")): b for b in brutos if b.get("nome")}
    saida = []
    for i, m in enumerate(corpo.medicamentos):
        bruto = brutos[i] if len(brutos) == len(corpo.medicamentos) else por_nome.get(_norm(m.medicamento), {})
        lista = _lista_controle_valida(bruto.get("lista_controle"))
        nome_n = _norm(m.medicamento)
        saida.append({
            "medicamento": m.medicamento,
            "controlado": (_verdadeiro(bruto.get("controlado")) or bool(lista)
                           or any(c in nome_n for c in POSSIVELMENTE_CONTROLADOS)),
            "lista_controle": lista,
            "recomendacoes": str(bruto.get("recomendacoes") or "").strip()[:300],
        })
    return {"itens": saida, "provedor": resp.provedor, "modelo": resp.modelo}


@router.post("/receita")
def salvar_receita(
    corpo: ReceitaSalvar,
    usuario_logado=Depends(exigir_perfil(PERFIS_CLINICOS)),
    db: Session = Depends(get_db),
):
    """Grava a receita revisada nas tabelas `prescricao` / `item_prescricao`."""
    existe = db.execute(text("SELECT 1 FROM consulta WHERE id = :i"), {"i": corpo.consulta_id}).first()
    if not existe:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    try:
        prescricao_id = db.execute(
            text("INSERT INTO prescricao (consulta_id, observacoes) VALUES (:c, :o) RETURNING id"),
            {"c": corpo.consulta_id, "o": corpo.observacoes},
        ).scalar_one()
        for item in corpo.itens:
            db.execute(
                text(
                    "INSERT INTO item_prescricao "
                    "(prescricao_id, medicamento, dosagem, frequencia, duracao, tipo_uso, observacoes) "
                    "VALUES (:p, :m, :d, :f, :u, :t, :o)"
                ),
                {"p": prescricao_id, "m": item.medicamento, "d": item.dosagem, "f": item.frequencia,
                 "u": item.duracao, "t": item.tipo_uso,
                 "o": _empacotar_observacoes(item.observacoes, item.recomendacoes, item.quantidade)},
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Erro ao salvar receita da consulta %s", corpo.consulta_id)
        raise HTTPException(status_code=500, detail="Erro ao salvar a receita.")
    return {"mensagem": "Receita salva no prontuário.", "prescricao_id": prescricao_id}


# --------------------------------------------------------------------------- #
# Exames
# --------------------------------------------------------------------------- #
CATEGORIAS_EXAME = {"laboratorial": "LABORATORIAL", "imagem": "IMAGEM", "outro": "OUTRO"}


def _garantir_triagem_brucella(exames: list, d: dict) -> list:
    """Cão com quadro reprodutivo (epididimite, orquite, piospermia, aborto, falha de concepção): garante
    a sorologia para Brucella canis (zoonose), mesmo que a IA a tenha esquecido."""
    if d.get("grupo") != "canino":
        return exames
    caso = _norm(_texto_do_caso(d))
    if not any(m in caso for m in MARCADORES_REPRODUCAO):
        return exames
    if any("brucel" in _norm(e["nome"]) for e in exames):
        return exames
    exames.append({
        "nome": "Sorologia para Brucella canis",
        "categoria": "LABORATORIAL",
        "prioridade": "URGENTE",
        "justificativa": "Triagem de brucelose canina em quadro reprodutivo (zoonose); confirmar por cultura ou PCR conforme o caso.",
    })
    exames.sort(key=lambda e: 0 if e["prioridade"] == "URGENTE" else 1)
    return exames


def _validar_estrutura_exames(dados: dict) -> dict:
    if not isinstance(dados.get("exames"), list):
        raise ValueError("JSON sem a lista 'exames'.")
    return dados


def _montar_exames(dados: dict) -> list:
    exames, vistos = [], set()
    for bruto in dados["exames"][:15]:
        if not isinstance(bruto, dict):
            continue
        nome = str(bruto.get("nome") or "").strip()[:150]
        chave = _norm(nome)
        if not nome or chave in vistos:
            continue
        vistos.add(chave)
        categoria = CATEGORIAS_EXAME.get(_norm(bruto.get("categoria")), "OUTRO")
        prioridade = "URGENTE" if _norm(bruto.get("prioridade")) == "urgente" else "ROTINA"
        exames.append({
            "nome": nome,
            "categoria": categoria,
            "prioridade": prioridade,
            "justificativa": corrigir_siglas(str(bruto.get("justificativa") or "").strip()[:300]),
        })
    # Urgentes primeiro, mantendo a ordem original dentro de cada grupo.
    exames.sort(key=lambda e: 0 if e["prioridade"] == "URGENTE" else 1)
    return exames


@router.post("/sugerir-exames")
def sugerir_exames(
    ctx: ContextoClinico,
    usuario_logado=Depends(exigir_perfil(PERFIS_CLINICOS)),
    db: Session = Depends(get_db),
):
    d = _carregar_contexto(db, ctx)
    _exigir_parecer(d)

    # Barragem sanitária: doença de notificação/controle oficial NÃO passa pelo LLM.
    # Só valem os exames oficiais (e nenhum preventivo/complementar fora do fluxo oficial).
    doenca = _doenca_oficial_do_caso(d, db)
    if doenca:
        logger.info("sugerir-exames: barragem aplicada (%s), LLM não consultado", doenca["nome"])
        oficiais = _montar_exames({"exames": [
            {"nome": e["nome"], "categoria": "laboratorial", "prioridade": "urgente",
             "justificativa": e["justificativa"]}
            for e in doenca["exames_lista"]
        ]})
        return {
            "exames": oficiais,
            "provedor": "barragem_sanitaria",
            "modelo": None,
            "doenca_oficial": doenca["nome"],
            "aviso": f"Suspeita de {doenca['nome']}: doença de notificação oficial. Somente os exames "
                     "do fluxo oficial são sugeridos; notifique o serviço veterinário oficial.",
        }

    preventivos = (
        "ATENÇÃO: O tutor solicitou check-up / exames preventivos nesta visita. "
        "Além dos exames para a suspeita principal, inclua também os exames de rotina adequados à espécie e idade.\n\n"
        if d.get("solicitar_exames_preventivos") else ""
    )

    prompt = (
        "Você é um médico veterinário clínico experiente no Brasil. Com base SOMENTE no caso "
        "abaixo, indique os exames complementares necessários para confirmar ou descartar a "
        "suspeita diagnóstica e avaliar a gravidade.\n\n"
        f"{preventivos}"
        f"CASO:\n{_texto_do_caso(d)}\n\n"
        "REGRAS OBRIGATÓRIAS:\n"
        "1. Peça só exames que mudem a conduta. Máximo de 6 (8 se o tutor solicitou check-up preventivo), do mais importante para o menos.\n"
        "1.1 ESPECIFICIDADE: cada exame deve ter alvo claro (ex.: \"PCR para Leptospira spp.\", e nunca \"PCR para patógenos comuns\"). É proibido pedir painéis genéricos ou exames de baixo rendimento para a suspeita (ex.: perfil hormonal sem suspeita endócrina). Não peça exame de confirmação cuja única função seja repetir outro já pedido; exame de segunda linha (ex.: PCR após cultura negativa) só se a justificativa disser quando solicitá-lo.\n"
        "2. Não repita exames que já constam como anexados, a menos que precisem ser repetidos "
        "(explique na justificativa).\n"
        "3. MEDICINA VETERINÁRIA (NÃO HUMANA): Respeite a fisiologia da espécie. É expressamente proibido pedir exames "
        "próprios da medicina humana (ex.: teste de intolerância à lactose em gato adulto; nessa situação o diagnóstico é clínico). "
        "Adeque os exames à espécie.\n"
        "4. categoria: \"LABORATORIAL\", \"IMAGEM\" ou \"OUTRO\". prioridade: \"URGENTE\" (precisa ser feito já) ou \"ROTINA\".\n"
        "5. justificativa: uma frase clínica curta ligando o exame ao caso. SILÊNCIO ESTRUTURAL: PROIBIDO meta-comentários. Não escreva 'A IA sugere' ou 'Solicitado para'. Vá direto ao ponto.\n"
        "6. EMBASAMENTO CIENTÍFICO: A escolha dos exames solicitados deve basear-se OBRIGATORIAMENTE nestas fontes oficiais: "
        "'MSD / Merck Veterinary Manual' (clínica geral); 'Cornell eClinPath' (patologia laboratorial); 'Embrapa' "
        "(animais de produção); e 'ExoCalc', 'Avian Exotics' ou 'Animal Diversity Web' (aves, exóticos e silvestres). "
        "Nunca utilize raciocínio da medicina humana.\n"
        "7. Se nenhum exame for necessário, devolva a lista vazia. Não escreva nada fora do JSON.\n\n"
        "FORMATO (JSON):\n"
        "{\"exames\": [{\"nome\": \"Hemograma completo\", \"categoria\": \"LABORATORIAL\", "
        "\"prioridade\": \"URGENTE\", \"justificativa\": \"Suspeita de infecção e estadiamento de anemia.\"}]}"
    )
    
    try:
        resp = gerar_json(db, prompt, validar=_validar_estrutura_exames)
    except IAIndisponivelError as exc:
        _erro_ia(exc)

    return {
        "exames": _garantir_triagem_brucella(_montar_exames(resp.dados), d),
        "provedor": resp.provedor,
        "modelo": resp.modelo,
        "aviso": "Sugestão gerada por IA. O veterinário decide quais exames solicitar.",
    }


# --------------------------------------------------------------------------- #
# Solicitação de exames: salvar e histórico por paciente
# --------------------------------------------------------------------------- #
class ExameSalvar(BaseModel):
    nome: str = Field(..., min_length=1, max_length=150)
    categoria: Optional[str] = Field("OUTRO", max_length=20)
    prioridade: Optional[str] = Field("ROTINA", max_length=10)
    justificativa: Optional[str] = Field(None, max_length=300)


class ExamesSalvar(BaseModel):
    consulta_id: int
    exames: List[ExameSalvar] = Field(..., min_length=1, max_length=30)


@router.post("/exames")
def salvar_exames(
    corpo: ExamesSalvar,
    usuario_logado=Depends(exigir_perfil(PERFIS_CLINICOS)),
    db: Session = Depends(get_db),
):
    """Grava a solicitação de exames (um 'lote') em `solicitacao_exame`."""
    existe = db.execute(text("SELECT 1 FROM consulta WHERE id = :i"), {"i": corpo.consulta_id}).first()
    if not existe:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    lote = str(uuid.uuid4())
    try:
        for ex in corpo.exames:
            categoria = (ex.categoria or "").upper()
            prioridade = (ex.prioridade or "").upper()
            db.execute(
                text(
                    "INSERT INTO solicitacao_exame "
                    "(consulta_id, lote, nome_exame, categoria, prioridade, justificativa) "
                    "VALUES (:c, :l, :n, :cat, :pri, :j)"
                ),
                {
                    "c": corpo.consulta_id, "l": lote, "n": ex.nome.strip(),
                    "cat": categoria if categoria in CATEGORIAS_EXAME.values() else "OUTRO",
                    "pri": prioridade if prioridade in ("URGENTE", "ROTINA") else "ROTINA",
                    "j": (ex.justificativa or "").strip() or None,
                },
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Erro ao salvar exames da consulta %s", corpo.consulta_id)
        raise HTTPException(status_code=500, detail="Erro ao salvar a solicitação de exames.")
    return {"mensagem": "Solicitação de exames salva no prontuário.", "lote": lote}


def _iso(valor):
    return valor.isoformat() if hasattr(valor, "isoformat") else valor


@router.get("/historico/{animal_id}")
def historico_do_paciente(
    animal_id: int,
    usuario_logado=Depends(exigir_perfil(PERFIS_CLINICOS)),
    db: Session = Depends(get_db),
):
    """
    Receitas e solicitações de exame do paciente, agrupadas por atendimento
    (os 50 atendimentos mais recentes). Só entram atendimentos que tenham receita ou exames.
    """
    if not db.execute(text("SELECT 1 FROM animal WHERE id = :i"), {"i": animal_id}).first():
        raise HTTPException(status_code=404, detail="Paciente (animal) não encontrado.")

    consultas = db.execute(
        text(
            "SELECT id, codigo, data_consulta, suspeita_diagnostica, peso_atendimento, usuario_id "
            "FROM consulta WHERE animal_id = :a ORDER BY data_consulta DESC, id DESC LIMIT 50"
        ),
        {"a": animal_id},
    ).mappings().all()
    if not consultas:
        return []

    ids = [c["id"] for c in consultas]
    por_consulta = {
        c["id"]: {
            "consulta_id": c["id"],
            "codigo": c["codigo"] or f"CNS-{c['id']:04d}",
            "data_consulta": _iso(c["data_consulta"]),
            "suspeita_diagnostica": c["suspeita_diagnostica"],
            "usuario_id": c["usuario_id"],
            "peso_atendimento": float(c["peso_atendimento"]) if c["peso_atendimento"] is not None else None,
            "receitas": [],
            "exames": [],
        }
        for c in consultas
    }

    receitas = db.execute(
        text("SELECT id, consulta_id, data_prescricao, observacoes FROM prescricao "
             "WHERE consulta_id IN :ids ORDER BY data_prescricao DESC, id DESC")
        .bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    ).mappings().all()
    itens_por_receita: dict = {}
    if receitas:
        itens = db.execute(
            text("SELECT prescricao_id, medicamento, dosagem, frequencia, duracao, tipo_uso, observacoes "
                 "FROM item_prescricao WHERE prescricao_id IN :ids ORDER BY id")
            .bindparams(bindparam("ids", expanding=True)),
            {"ids": [r["id"] for r in receitas]},
        ).mappings().all()
        for item in itens:
            registro = {k: item[k] for k in ("medicamento", "dosagem", "frequencia", "duracao", "tipo_uso")}
            registro.update(_desempacotar_observacoes(item["observacoes"]))
            itens_por_receita.setdefault(item["prescricao_id"], []).append(registro)
    for r in receitas:
        por_consulta[r["consulta_id"]]["receitas"].append({
            "id": r["id"],
            "data": _iso(r["data_prescricao"]),
            "observacoes": r["observacoes"],
            "itens": itens_por_receita.get(r["id"], []),
        })

    exames = db.execute(
        text("SELECT consulta_id, lote, nome_exame, categoria, prioridade, justificativa, data_solicitacao "
             "FROM solicitacao_exame WHERE consulta_id IN :ids ORDER BY data_solicitacao DESC, id")
        .bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    ).mappings().all()
    lotes: dict = {}
    for e in exames:
        bloco = por_consulta[e["consulta_id"]]
        lote = lotes.get(str(e["lote"]))
        if lote is None:
            lote = {"lote": str(e["lote"]), "data": _iso(e["data_solicitacao"]), "itens": []}
            lotes[str(e["lote"])] = lote
            bloco["exames"].append(lote)
        lote["itens"].append({
            "nome": e["nome_exame"], "categoria": e["categoria"],
            "prioridade": e["prioridade"], "justificativa": e["justificativa"],
        })

    return [b for b in por_consulta.values() if b["receitas"] or b["exames"]]