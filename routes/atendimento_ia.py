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

from database.database import get_db
from models.animais import Animal
from services.ia_service import IAIndisponivelError, gerar_json
from services.security import exigir_perfil

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


def _quantidade_total(dose, peso, conc_valor, conc_unidade, freq_horas, dias) -> Optional[str]:
    """Quantidade a dispensar para o tratamento completo (só quando há dados para calcular)."""
    conc, h, d = _numero(conc_valor), _numero(freq_horas), _numero(dias)
    if dose is None or peso is None or conc is None or h is None or d is None:
        return None
    tomadas = max(1, math.ceil(d * 24 / h))
    por_tomada_mg = dose * peso
    if conc_unidade == "mg/mL":
        total_ml = por_tomada_mg / conc * tomadas
        return f"1 (um) frasco — volume do tratamento completo ≈ {_fmt(total_ml)} mL"
    if conc_unidade in ("mg/comprimido", "mg/cápsula"):
        unidade = "comprimido" if conc_unidade == "mg/comprimido" else "cápsula"
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
    """Número no padrão brasileiro, sem zeros sobrando (2,5 / 0,4 / 12)."""
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
def _validar_estrutura_receita(dados: dict) -> dict:
    if not isinstance(dados.get("medicamentos"), list):
        raise ValueError("JSON sem a lista 'medicamentos'.")
    return dados


def _montar_receita(dados: dict, peso: Optional[float], grupo: str) -> dict:
    """Valida cada medicamento sugerido, calcula a dose e aplica as travas de segurança."""
    itens, alertas = [], []
    vistos = set()
    proibidos = PROIBIDOS_POR_GRUPO.get(grupo, PROIBIDOS_POR_GRUPO["outro"])

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
        if nome_n in vistos:
            continue
        vistos.add(nome_n)

        apresentacao = str(bruto.get("apresentacao") or "").strip()[:150]
        titulo = f"{nome} {apresentacao}".strip()

        conc_unidade = UNIDADES_CONCENTRACAO.get(_norm(bruto.get("concentracao_unidade")))
        dose = _numero(bruto.get("dose_mg_kg"))
        posologia = str(bruto.get("posologia_texto") or "").strip()[:300]

        if dose is not None and dose > DOSE_MAXIMA_MG_KG:
            alertas.append(f"{nome}: dose de {_fmt(dose)} mg/kg fora do plausível, item descartado.")
            continue
        if dose is not None:
            dosagem = _calcular_dosagem(dose, peso, bruto.get("concentracao_valor"),
                                        conc_unidade, alertas, nome)
        elif posologia:
            dosagem = posologia
        else:
            dosagem = "Definir a dose com o veterinário"
            alertas.append(f"{nome}: a IA não indicou dose segura. Defina a dose manualmente.")

        tipo = _norm(bruto.get("disponibilidade")).upper().replace(" ", "_")
        if tipo not in TIPO_USO_ROTULO:
            tipo = "A_CONFIRMAR"

        via = str(bruto.get("via") or "").strip().lower()[:40]
        obs = str(bruto.get("observacao") or "").strip()[:400]
        observacoes = " ".join(p for p in (f"Via: {via}." if via else "", obs) if p)

        recomendacoes = str(bruto.get("recomendacoes") or "").strip()[:300]
        lista_controle = _lista_controle_valida(bruto.get("lista_controle"))
        # A IA classifica; a lista fixa é só uma rede de segurança contra falso negativo.
        controlado = (_verdadeiro(bruto.get("controlado")) or bool(lista_controle)
                      or any(c in nome_n for c in POSSIVELMENTE_CONTROLADOS))
        quantidade = None
        if controlado:
            alertas.append(
                f"{nome}: medicamento de controle especial"
                f"{f' (lista {lista_controle})' if lista_controle else ''} (Portaria SVS/MS 344/98). "
                "Confira a lista vigente e a quantidade antes de emitir."
            )
            quantidade = _quantidade_total(dose, peso, bruto.get("concentracao_valor"), conc_unidade,
                                           bruto.get("frequencia_horas"), bruto.get("duracao_dias"))

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

    for aviso in dados.get("alertas") or []:
        if isinstance(aviso, str) and aviso.strip():
            alertas.append(aviso.strip()[:300])
    return {"itens": itens, "alertas": alertas[:15]}


@router.post("/sugerir-receita")
def sugerir_receita(
    ctx: ContextoClinico,
    usuario_logado=Depends(exigir_perfil(PERFIS_CLINICOS)),
    db: Session = Depends(get_db),
):
    d = _carregar_contexto(db, ctx)
    _exigir_parecer(d)

    prompt = (
        "Você é um médico veterinário clínico experiente no Brasil, apoiando a elaboração de uma "
        "receita. Com base SOMENTE no caso abaixo, sugira o tratamento medicamentoso necessário.\n\n"
        f"CASO:\n{_texto_do_caso(d)}\n\n"
        "REGRAS OBRIGATÓRIAS:\n"
        "1. Sugira apenas medicamentos justificados pelo parecer clínico. Se não houver indicação "
        "de medicação, devolva a lista vazia. Prefira poucos itens (no máximo 6).\n"
        "2. Doses e segurança dependem da ESPÉCIE. Considere contraindicações (ex.: felinos e "
        "paracetamol/permetrina; cães e ibuprofeno).\n"
        "3. NÃO invente dose. Para espécie exótica/silvestre sem dose bem estabelecida na "
        "literatura, deixe dose_mg_kg como null e escreva em posologia_texto "
        "\"definir dose com o veterinário\".\n"
        "4. Informe a dose em mg/kg POR TOMADA (dose_mg_kg) e a concentração do produto "
        "comercial (concentracao_valor + concentracao_unidade: \"mg/mL\", \"mg/comprimido\" ou "
        "\"mg/capsula\"). NÃO calcule mg totais, mL nem frações: o sistema calcula. Para uso "
        "tópico, oftálmico ou sem dose em mg/kg, deixe dose_mg_kg null e descreva em posologia_texto.\n"
        "5. frequencia_horas é o intervalo em horas (24, 12, 8, 6...). duracao_dias é um número.\n"
        "6. disponibilidade: \"HUMANO\" (comum em farmácia/drogaria), \"VETERINARIO\" (vendido em "
        "pet shop, agropecuária ou clínica veterinária) ou \"CLINICA\" (injetável aplicado na clínica).\n"
        "7. Evite medicamentos de controle especial, a menos que sejam realmente necessários.\n"
        "8. controlado: true se o princípio ativo consta nas listas de controle especial da Portaria "
        "SVS/MS 344/98 (A1, A2, A3, B1, B2, C1, C2, C3, C4 ou C5); nesse caso informe a lista em "
        "lista_controle. Caso contrário, controlado false e lista_controle null.\n"
        "9. recomendacoes: orientações práticas de administração para ESTE paciente: relação com "
        "alimentação ou jejum, horário, intervalo em relação aos OUTROS medicamentos desta mesma receita, "
        "interações e sinais para suspender e procurar a clínica. Máximo de 250 caracteres, em português, "
        "sem repetir dose nem frequência. Inclua apenas conhecimento consolidado; se não houver nada "
        "relevante, use null.\n"
        "10. ATENÇÃO ÀS APRESENTAÇÕES COMERCIAIS REAIS NO BRASIL: Não invente concentrações inexistentes. "
        "Exemplo crítico: Ondansetrona NÃO possui solução oral pronta no mercado brasileiro. Prescreva os comprimidos (ex: 4 mg ou 8 mg), "
        "a solução injetável (2 mg/mL) com indicação para uso oral (off-label comum), ou indique formulação manipulada.\n"
        "11. PRIORIDADE PARA VIA ORAL: Como o tratamento é domiciliar, priorize SEMPRE apresentações para uso oral (comprimidos, cápsulas, gotas ou suspensão). NÃO prescreva formulações injetáveis (IV, IM, SC) para uso em casa, pois o tutor não consegue administrar. Se for usar injetável, que seja estritamente para aplicação hospitalar (disponibilidade: \"CLINICA\").\n"
        "12. Não escreva nada fora do JSON.\n\n"
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

    receita = _montar_receita(resp.dados, d.get("peso"), d["grupo"])
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
    linhas = "\n".join(
        f"{i + 1}. {m.medicamento}"
        f" | dosagem: {m.dosagem or '-'} | frequência: {m.frequencia or '-'} | duração: {m.duracao or '-'}"
        for i, m in enumerate(corpo.medicamentos)
    )
    prompt = (
        "Você é um médico veterinário clínico experiente no Brasil, revisando uma receita.\n\n"
        f"PACIENTE E CASO:\n{_texto_do_caso(d)}\n\n"
        f"MEDICAMENTOS DA RECEITA:\n{linhas}\n\n"
        "Para CADA medicamento, na MESMA ORDEM e com o mesmo nome:\n"
        "1. controlado: true se o princípio ativo consta nas listas de controle especial da Portaria "
        "SVS/MS 344/98 (A1, A2, A3, B1, B2, C1, C2, C3, C4 ou C5), informando a lista em lista_controle; "
        "caso contrário false e null.\n"
        "2. recomendacoes: orientações práticas de administração para ESTE paciente (espécie, peso e idade): "
        "relação com alimentação ou jejum, horário, intervalo em relação aos OUTROS medicamentos desta "
        "receita, interações e sinais para suspender e procurar a clínica. Máximo de 250 caracteres, em "
        "português, sem repetir dose nem frequência. Apenas conhecimento consolidado; se não houver nada "
        "relevante, use null.\n"
        "Não escreva nada fora do JSON.\n\n"
        "FORMATO (JSON):\n"
        "{\"medicamentos\": [{\"nome\": \"Sucralfato\", \"controlado\": false, \"lista_controle\": null, "
        "\"recomendacoes\": \"Manter intervalo de 2 horas em relação a outros medicamentos.\"}]}"
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
            "justificativa": str(bruto.get("justificativa") or "").strip()[:300],
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

    preventivos = (
        "O tutor pediu check-up / exames preventivos nesta visita: inclua também os exames "
        "preventivos adequados à espécie e à idade.\n"
        if d.get("solicitar_exames_preventivos") else ""
    )
    prompt = (
        "Você é um médico veterinário clínico experiente no Brasil. Com base SOMENTE no caso "
        "abaixo, indique os exames complementares necessários para confirmar ou descartar a "
        "suspeita diagnóstica e avaliar a gravidade.\n\n"
        f"CASO:\n{_texto_do_caso(d)}\n\n"
        "REGRAS OBRIGATÓRIAS:\n"
        "1. Peça só exames que mudem a conduta. Máximo de 8, do mais importante para o menos.\n"
        "2. Não repita exames que já constam como anexados, a menos que precisem ser repetidos "
        "(explique na justificativa).\n"
        "3. Adeque os exames à espécie (ex.: valores e exames de rotina de répteis e aves diferem "
        "dos de cães e gatos).\n"
        f"{preventivos}"
        "4. categoria: \"LABORATORIAL\", \"IMAGEM\" ou \"OUTRO\". prioridade: \"URGENTE\" (precisa "
        "ser feito já) ou \"ROTINA\".\n"
        "5. justificativa: uma frase curta ligando o exame ao caso.\n"
        "6. Se nenhum exame for necessário, devolva a lista vazia. Não escreva nada fora do JSON.\n\n"
        "FORMATO (JSON):\n"
        "{\"exames\": [{\"nome\": \"Hemograma completo\", \"categoria\": \"LABORATORIAL\", "
        "\"prioridade\": \"URGENTE\", \"justificativa\": \"Avaliar infecção e anemia.\"}]}"
    )
    try:
        resp = gerar_json(db, prompt, validar=_validar_estrutura_exames)
    except IAIndisponivelError as exc:
        _erro_ia(exc)

    return {
        "exames": _montar_exames(resp.dados),
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
            "SELECT id, codigo, data_consulta, suspeita_diagnostica, peso_atendimento "
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