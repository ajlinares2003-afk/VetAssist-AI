"""
services/referencia_auto.py

Quando o animal NÃO tem perfil na biblioteca oficial, a IA (Gemini com pesquisa na web)
procura os parâmetros na literatura utilizando a chave multivariada:
Nome Científico + Porte + Sexo + Faixa etária (idade), restrita às fontes oficiais da clínica
(services/fontes_oficiais.py, conferidas no código a partir das páginas realmente consultadas),
e cadastra o resultado na `biblioteca_parametros_oficiais` com status PENDENTE.

Regras de segurança deste fluxo:
  - Só aceita resposta ANCORADA em fontes reais da web (sem fontes = descartada).
  - Confiança "baixa" ou literatura insuficiente = nada é cadastrado.
  - O registro nasce como RASCUNHO: aparece na tela para o enfermeiro, mas NÃO entra na
    biblioteca ainda. Ao FINALIZAR a triagem ele vira PENDENTE (promover_rascunho) e só
    então o veterinário o valida (routers/referencias_admin.py).
  - Raça genérica (SRD) só é pesquisada se houver NOME CIENTÍFICO: o perfil nasce do
    nome científico (+ porte, para cães), nunca de "SRD".
  - Reaproveita o perfil existente (mesmo texto de `especie`) quando só falta o sexo,
    para não duplicar perfis e quebrar a busca.
"""
import json
import logging
import re
import time
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.biblioteca_referencia import (
    GENERICAS, _escolher_descritor, _faixa_da_linha, _montar_descritores, _norm, _partes,
    _sexo_chave,
)
from services.faixa_etaria import FAIXAS, ROTULO as ROTULO_FAIXA, faixa_e_aplicavel
from services.fontes_oficiais import (
    ESTRITO as FONTES_ESTRITAS, dominios_vistos, fontes_da_lista,
    identificar_grupo, texto_prioridade,
)
from services.ia_service import IAIndisponivelError, gerar_json

logger = logging.getLogger("vetassist.referencia_auto")

ECC_IDEAL_PADRAO = "5 (Escala 1 a 9)"  # regra da clínica

# Pausa após uma pesquisa que falhou. Cada tentativa gasta tokens/cota das IAs (a busca da
# Groq é cara); sem pausa, reabrir a tela repetiria a chamada e esgotaria o limite diário.
COOLDOWN_FALHA_SEGUNDOS = 20 * 60
_falhas_recentes: dict = {}  # (perfil, sexo) -> (instante, motivo)  [por processo]

# (campo, é_inteiro). FC e FR são int4 no banco; peso e temperatura são numeric.
CAMPOS_NUMERICOS = (
    ("peso_min", False), ("peso_max", False),
    ("temp_repouso_min", False), ("temp_repouso_max", False),
    ("temp_clinica_min", False), ("temp_clinica_max", False),
    ("fc_repouso_min", True), ("fc_repouso_max", True),
    ("fc_clinica_min", True), ("fc_clinica_max", True),
    ("fr_repouso_min", True), ("fr_repouso_max", True),
    ("fr_clinica_min", True), ("fr_clinica_max", True),
)

# Limites só para barrar lixo de formatação (ex.: "7-14" lido como 714).
# NÃO são regra clínica; quem valida o conteúdo é o veterinário.
SANIDADE = {"peso": (0.001, 10000), "temp": (25, 48), "fc": (5, 1000), "fr": (1, 500)}


# --------------------------------------------------------------------------- #
# Validação da resposta da IA
# --------------------------------------------------------------------------- #
def _numero(valor, inteiro: bool):
    if isinstance(valor, bool):
        raise ValueError("valor booleano")
    if valor is None:
        raise ValueError("valor ausente")
    texto = str(valor).strip()
    if texto == "":
        raise ValueError("valor vazio")
    try:
        numero = float(texto.replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError("valor não numérico")
    return int(round(numero)) if inteiro else round(numero, 2)


def validar_pesquisa(dados: dict) -> dict:
    """Normaliza e valida o JSON da pesquisa. Levanta ValueError se não servir."""
    if not isinstance(dados, dict):
        raise ValueError("JSON da pesquisa não é um objeto.")

    confianca = _norm(dados.get("confianca"))
    if confianca not in ("alta", "media"):
        raise ValueError("Literatura insuficiente (confiança baixa ou ausente).")

    saida = {}
    for campo, inteiro in CAMPOS_NUMERICOS:
        try:
            saida[campo] = _numero(dados.get(campo), inteiro)
        except (TypeError, ValueError):
            raise ValueError(f"Campo numérico inválido: {campo}")

    for base in ("peso", "temp_repouso", "temp_clinica", "fc_repouso", "fc_clinica",
                 "fr_repouso", "fr_clinica"):
        minimo, maximo = saida[f"{base}_min"], saida[f"{base}_max"]
        tipo = "temp" if base.startswith("temp") else base.split("_")[0]
        baixo, alto = SANIDADE[tipo]
        if not (baixo <= minimo <= maximo <= alto):
            raise ValueError(f"Faixa inconsistente em {base}: {minimo}-{maximo}")

    tpc = str(dados.get("tpc_ref") or "").strip()
    if not tpc:
        raise ValueError("tpc_ref ausente.")
    if not (re.search(r"\d", tpc) and re.search(r"seg|\d\s*s\b", tpc.lower())) or "°" in tpc:
        raise ValueError("tpc_ref não parece ser um tempo em segundos.")
    saida["tpc_ref"] = tpc

    for campo in ("mucosas_ref", "classe_animal", "grupo", "nome_cientifico"):
        valor = str(dados.get(campo) or "").strip()
        if not valor:
            raise ValueError(f"Campo ausente: {campo}")
        saida[campo] = valor

    citadas = dados.get("fontes_citadas")
    if not isinstance(citadas, list) or not any(isinstance(f, dict) and f.get("titulo") for f in citadas):
        raise ValueError("Nenhuma fonte bibliográfica citada.")

    saida["fontes_citadas"] = []
    for f in citadas[:8]:
        if not isinstance(f, dict):
            continue
        titulo = str(f.get("titulo") or "").strip()
        if not titulo:
            continue
        saida["fontes_citadas"].append({
            "titulo": titulo[:300],
            "autores": str(f.get("autores") or "").strip()[:400],
            "ano": str(f.get("ano") or "").strip()[:20],
            "url": str(f.get("url") or "").strip()[:500],
        })

    if not saida["fontes_citadas"]:
        raise ValueError("Nenhuma fonte bibliográfica citada.")

    saida["confianca"] = confianca
    saida["observacoes"] = str(dados.get("observacoes") or "").strip()[:1000]
    return saida


# --------------------------------------------------------------------------- #
# Motivo da falha em linguagem simples
# --------------------------------------------------------------------------- #
def _resumo_tentativa(texto: str) -> str:
    """'groq_1/openai/gpt-oss-120b: RateLimitError | ...' -> 'groq_1: limite diário de tokens (TPD) atingido'."""
    origem, _, detalhe = texto.partition(": ")
    nome = origem.split("/")[0] or origem
    t = detalhe.lower()
    espera = re.search(r"try again in ([0-9a-z\.]+)", t)
    liberado = f" (liberado em {espera.group(1).rstrip('.')})" if espera else ""
    if "tokens per day" in t or "(tpd)" in t:
        motivo = "limite diário de tokens (TPD) atingido" + liberado
    elif "tokens per minute" in t or "(tpm)" in t:
        motivo = "limite de tokens por minuto (TPM) atingido" + liberado
    elif "rate_limit" in t or "rate limit" in t:
        motivo = "limite de uso da IA atingido" + liberado
    elif "api key not valid" in t or "invalid api key" in t or "invalid_api_key" in t or "401" in t:
        motivo = "chave de API inválida: confira a chave em Configurações de IA"
    elif "not_found" in t or "no longer available" in t or "404" in t:
        motivo = "modelo indisponível para esta conta: troque o modelo em Configurações de IA"
    elif "resource_exhausted" in t or "429" in t:
        motivo = "cota esgotada ou pesquisa do Google indisponível no plano atual (429)"
    elif "413" in t or "request too large" in t:
        motivo = "requisição grande demais para o limite do plano"
    elif "sem consultar fontes" in t:
        motivo = "a IA respondeu sem pesquisar na web"
    elif "fonte oficial da lista" in t:
        motivo = "a IA não consultou as fontes oficiais da clínica. " + detalhe.split("| ")[-1][:200]
    elif "literatura insuficiente" in t:
        motivo = "literatura insuficiente para este perfil"
    else:
        motivo = detalhe[:140]
    return f"{nome}: {motivo}"


def _motivo_das_tentativas(tentativas: list) -> str:
    return "a pesquisa falhou em todos os provedores. " + "; ".join(_resumo_tentativa(t) for t in tentativas[:3])


def limpar_falhas_pesquisa() -> None:
    """Chamada quando a configuração de IA muda (ou um teste de conexão passa): libera novas tentativas."""
    _falhas_recentes.clear()


# --------------------------------------------------------------------------- #
# Auxiliares
# --------------------------------------------------------------------------- #
def _sexo_da_biblioteca(sexo) -> str:
    n = _sexo_chave(sexo)  # aceita "M"/"F" do cadastro e "macho"/"fêmea"
    if n == "macho":
        return "macho"
    if n == "femea":
        return "fêmea"
    return "indiferente"


def _nome_do_perfil(raca) -> Optional[str]:
    """'Caracal - Lince do Deserto' -> 'Caracal (Lince do Deserto)'. Genérica -> None."""
    texto = str(raca or "").strip()
    if not texto:
        return None

    # aliases fortes para raça genérica ou indefinida
    aliases = {
        "srd", "sem raça", "sem raca", "sem raça definida", "sem raca definida",
        "mestiço", "mestico", "vazio", "indefinido", "nao informado", "não informado",
        "sem informação", "sem informacao", "generic", "genérico", "generico",
        "mixed", "cruza", "sem perfil", "sem raça conhecida"
    }

    chave = _norm(texto)
    if chave in aliases or chave.startswith("srd"):
        return None

    partes = [p.strip() for p in re.split(r"\s+-\s+", texto) if p.strip()]
    if not partes:
        return None

    primeira = partes[0]
    if _norm(primeira) in GENERICAS:
        return None

    return f"{primeira} ({' - '.join(partes[1:])})" if len(partes) > 1 else primeira


def _perfil_existente(linhas, raca):
    """Descritor que já responde por essa raça (para só acrescentar o sexo que falta)."""
    partes_raca = {p for p in _partes(raca) if p not in GENERICAS}
    return [d for d in _montar_descritores(linhas) if partes_raca & (d.aliases | d.exemplos)]


def _nome_do_perfil_generico(especie, sub_especie, nome_cientifico, porte) -> Optional[str]:
    """
    Raça genérica (SRD) com nome científico: 'Felino Doméstico (Felis catus)'.
    Cães levam o porte no nome ('Canino porte Médio (Canis lupus familiaris)'), pois mudam por porte.
    """
    cient = " ".join(str(nome_cientifico or "").split())
    if not cient:
        return None
    base = " ".join(str(sub_especie or especie or "").split()) or cient
    if _norm(cient).startswith("canis") and str(porte or "").strip():
        base = f"{base} porte {str(porte).strip()}"
    return f"{base} ({cient})"


def _montar_prompt(nome, sexo, porte, sub_especie, especie, nome_cientifico=None,
                   idade=None, faixa_etaria="adulto") -> str:
    grupo_animal = identificar_grupo(especie, sub_especie)
    fontes_prioritarias = texto_prioridade(grupo_animal)
    faixa_txt = ROTULO_FAIXA.get(faixa_etaria, "Adulto")
    idade_txt = f"{idade} anos" if idade is not None else "não informada"
    aplica_faixa = faixa_e_aplicavel(especie, sub_especie, nome_cientifico)

    return (
        "Você é um pesquisador de medicina veterinária de alta exigência hospitalar. "
        "⚠️ REGRA SUPREMA: PESQUISE PRIMEIRO E PRINCIPALMENTE NAS FONTES OFICIAIS ABAIXO, "
        f"nesta ordem de prioridade, para o grupo ({grupo_animal}). Use buscas com o nome científico "
        "e o termo 'site:<domínio>' de cada fonte:\n"
        f"{fontes_prioritarias}\n"
        "Só use bases acadêmicas (PubMed, SciELO, Google Scholar) como complemento, e cite quais. "
        "NUNCA use blogs, lojas, sites genéricos ou fóruns.\n\n"
        "Busque os parâmetros fisiológicos de referência para este paciente (chave multivariada):\n\n"
        f"- Nome Científico (taxonomia obrigatória): {nome_cientifico or 'Não informado'}\n"
        f"- Perfil / Raça: {nome}\n"
        f"- Espécie / Sub-espécie: {especie or 'não informada'} / {sub_especie or 'não informada'}\n"
        f"- Porte: {porte or 'não informado'}\n"
        f"- Sexo: {sexo or 'indiferente'}\n"
        f"- Idade: {idade_txt} -> FAIXA ETÁRIA A PESQUISAR: {faixa_txt.upper()}\n\n"
        "REGRAS CLÍNICAS:\n"
        f"- Os parâmetros devem ser os da faixa etária {faixa_txt.upper()}"
        + ("" if aplica_faixa else
           " (para esta espécie não há corte de idade fixo: forneça os de ADULTO e, em observacoes, "
           "diga se a idade informada muda os valores)") +
        ".\n"
        "- Respeite as variações de porte, sexo e faixa etária descritas na literatura.\n"
        "- peso_min/peso_max = faixa de peso do ADULTO desta espécie/porte (referência de condição "
        "corporal; não é peso de filhote).\n"
        "- 'repouso' = animal calmo, sem estresse; 'clinica' = durante o atendimento/manejo hospitalar.\n"
        "- Se as fontes não trouxerem os parâmetros para esta combinação, responda obrigatoriamente "
        "com \"confianca\": \"baixa\" (nada será cadastrado). NÃO estime nem invente valores.\n"
        "- Números com ponto decimal. FC em bpm, FR em ir/min, temperatura em °C, peso em kg.\n"
        "- O campo tpc_ref traz SOMENTE o tempo de preenchimento capilar em segundos.\n"
        "- Cite cada publicação usada em fontes_citadas, com título, autores, ano e URL (se houver).\n\n"
        "Responda SOMENTE com um objeto JSON puro, exatamente com estas chaves:\n"
        "{\n"
        "  \"classe_animal\": \"Mamífero | Ave | Réptil | ...\",\n"
        "  \"grupo\": \"ex.: Cães de Grande Porte / Canidae\",\n"
        "  \"nome_cientifico\": \"Gênero espécie\",\n"
        "  \"peso_min\": 0, \"peso_max\": 0,\n"
        "  \"temp_repouso_min\": 0, \"temp_repouso_max\": 0, \"temp_clinica_min\": 0, \"temp_clinica_max\": 0,\n"
        "  \"fc_repouso_min\": 0, \"fc_repouso_max\": 0, \"fc_clinica_min\": 0, \"fc_clinica_max\": 0,\n"
        "  \"fr_repouso_min\": 0, \"fr_repouso_max\": 0, \"fr_clinica_min\": 0, \"fr_clinica_max\": 0,\n"
        "  \"tpc_ref\": \"Até X segundos\",\n"
        "  \"mucosas_ref\": \"descrição curta\",\n"
        "  \"fontes_citadas\": [{\"titulo\": \"\", \"autores\": \"\", \"ano\": \"\", \"url\": \"\"}],\n"
        "  \"confianca\": \"alta | media | baixa\",\n"
        "  \"observacoes\": \"limitações dos dados e considerações de porte/idade, em 1 ou 2 frases\"\n"
        "}"
    )


_INSERIR = text("""
    INSERT INTO biblioteca_parametros_oficiais
      (classe_animal, grupo, especie, nome_cientifico, sexo, faixa_etaria, peso_min, peso_max,
       temp_repouso_min, temp_repouso_max, temp_clinica_min, temp_clinica_max,
       fc_repouso_min, fc_repouso_max, fc_clinica_min, fc_clinica_max,
       fr_repouso_min, fr_repouso_max, fr_clinica_min, fr_clinica_max,
       tpc_ref, mucosas_ref, ecc_ideal, fonte_bibliografica,
       status, origem, fontes, observacoes_ia)
    VALUES
      (:classe_animal, :grupo, :especie, :nome_cientifico, :sexo, :faixa_etaria, :peso_min, :peso_max,
       :temp_repouso_min, :temp_repouso_max, :temp_clinica_min, :temp_clinica_max,
       :fc_repouso_min, :fc_repouso_max, :fc_clinica_min, :fc_clinica_max,
       :fr_repouso_min, :fr_repouso_max, :fr_clinica_min, :fr_clinica_max,
       :tpc_ref, :mucosas_ref, :ecc_ideal, :fonte_bibliografica,
       'RASCUNHO', 'IA', CAST(:fontes AS jsonb), :observacoes_ia)
    ON CONFLICT (especie, sexo, faixa_etaria) DO NOTHING
    RETURNING id
""")


# --------------------------------------------------------------------------- #
# API pública
# --------------------------------------------------------------------------- #
def metadados_da_linha(db: Session, linha_id: int) -> dict:
    """status/origem/fontes da linha. Se a migração ainda não rodou, assume VALIDADO/MANUAL."""
    try:
        r = db.execute(
            text("SELECT status, origem, fontes, observacoes_ia "
                 "FROM biblioteca_parametros_oficiais WHERE id = :i"),
            {"i": linha_id},
        ).mappings().first()
        if r:
            return dict(r)
    except Exception:
        db.rollback()
        logger.warning("Colunas de status ausentes. Rode a migração da biblioteca.")
    return {"status": "VALIDADO", "origem": "MANUAL", "fontes": None, "observacoes_ia": None}


def pesquisar_e_registrar(db: Session, *, especie, sub_especie, raca, sexo, porte,
                          nome_cientifico=None, idade=None, faixa_etaria="adulto"):
    """
    Pesquisa na literatura utilizando a chave multivariada
    (Nome Científico + Porte + Sexo + Faixa etária/Idade) e guarda o resultado como RASCUNHO.
    """
    try:
        return _pesquisar_e_registrar(db, especie, sub_especie, raca, sexo, porte,
                                      nome_cientifico, idade, faixa_etaria)
    except Exception as exc:
        db.rollback()
        logger.exception("Falha na pesquisa automática de referência")
        return None, (f"erro ao cadastrar a referência ({type(exc).__name__}). "
                      "Confirme se a migração da biblioteca (faixa_etaria) foi executada.")


def _pesquisar_e_registrar(db, especie, sub_especie, raca, sexo, porte, nome_cientifico,
                           idade, faixa):
    from models.biblioteca_oficial import BibliotecaParametrosOficiais as Bib

    faixa = faixa if faixa in FAIXAS else "adulto"
    sexo_bib = _sexo_da_biblioteca(sexo)
    linhas = db.query(Bib).order_by(Bib.id).all()
    raca_generica = _nome_do_perfil(raca) is None

    # 1) Já existe um perfil para este animal? Reaproveita o MESMO texto de `especie`
    #    (só falta a linha do sexo/faixa etária), para não duplicar perfis.
    if raca_generica:
        descritor = _escolher_descritor(_montar_descritores(linhas), especie, sub_especie,
                                        raca, porte, nome_cientifico)
    else:
        existentes = _perfil_existente(linhas, raca)
        if len(existentes) > 1:
            logger.warning("Raça %r casa com mais de um perfil; não cadastrar.", raca)
            return None, "a raça casa com mais de um perfil da biblioteca; revise os perfis duplicados."
        descritor = existentes[0] if existentes else None
    base = descritor.linhas[0] if descritor else None

    # 2) Sem perfil: dá nome a um novo. SRD só é aceito com nome científico.
    if base:
        especie_perfil = base.especie
        if all(_sexo_chave(getattr(l, "sexo", "")) == "indiferente" for l in descritor.linhas):
            sexo_bib = "indiferente"  # perfil que não separa por sexo: uma pesquisa serve a todos
    else:
        especie_perfil = (_nome_do_perfil(raca) if not raca_generica else
                          _nome_do_perfil_generico(especie, sub_especie, nome_cientifico, porte))
        if not especie_perfil:
            return None, ("raça genérica (SRD) e sem nome científico no cadastro: "
                          "edite o animal para gerar o nome científico ou cadastre um perfil curado.")

    # Concorrência: alguém pode já ter cadastrado este perfil + sexo + faixa etária.
    ja_existe = next((l for l in linhas
                      if l.especie == especie_perfil and _norm(l.sexo) == _norm(sexo_bib)
                      and _faixa_da_linha(l) == faixa), None)
    if ja_existe:
        return ja_existe, None

    # Pausa após falha recente do mesmo perfil
    chave_falha = (_norm(especie_perfil), _norm(sexo_bib), faixa)
    anterior = _falhas_recentes.get(chave_falha)
    if anterior:
        decorrido = time.monotonic() - anterior[0]
        if decorrido < COOLDOWN_FALHA_SEGUNDOS:
            minutos = int((COOLDOWN_FALHA_SEGUNDOS - decorrido) // 60) + 1
            return None, (f"{anterior[1]} | nova tentativa automática em ~{minutos} min "
                          "(para não gastar a cota das IAs)")
        _falhas_recentes.pop(chave_falha, None)

    grupo = identificar_grupo(especie, sub_especie)

    def _exigir_fonte_da_lista(fontes_web):
        if not fontes_da_lista(fontes_web, grupo):
            raise ValueError("nenhuma fonte oficial da lista foi consultada "
                             f"(sites consultados: {dominios_vistos(fontes_web)})")

    try:
        resposta = gerar_json(
            db,
            _montar_prompt(
                especie_perfil, sexo_bib, porte, sub_especie, especie,
                nome_cientifico=nome_cientifico or getattr(base, "nome_cientifico", None),
                idade=idade, faixa_etaria=faixa,
            ),
            validar=validar_pesquisa,
            pesquisa_web=True,
            validar_fontes=_exigir_fonte_da_lista if FONTES_ESTRITAS else None,
        )
    except IAIndisponivelError as exc:
        logger.warning("Pesquisa automática sem resultado: %s | %s", exc, exc.tentativas)
        motivo = _motivo_das_tentativas(exc.tentativas) if exc.tentativas else str(exc)
        _falhas_recentes[chave_falha] = (time.monotonic(), motivo)
        return None, motivo
    _falhas_recentes.pop(chave_falha, None)

    d = resposta.dados
    oficiais = fontes_da_lista(resposta.fontes, grupo)
    nomes_oficiais = ", ".join(dict.fromkeys(o["fonte"] for o in oficiais)) or "fora da lista oficial"
    titulos = "; ".join(str(f.get("titulo"))[:120] for f in d["fontes_citadas"][:3] if f.get("titulo"))
    parametros = {
        **{campo: d[campo] for campo, _ in CAMPOS_NUMERICOS},
        "classe_animal": getattr(base, "classe_animal", None) or d["classe_animal"],
        "grupo": getattr(base, "grupo", None) or d["grupo"],
        "nome_cientifico": nome_cientifico or getattr(base, "nome_cientifico", None) or d["nome_cientifico"],
        "especie": especie_perfil,
        "sexo": sexo_bib,
        "faixa_etaria": faixa,
        "tpc_ref": d["tpc_ref"],
        "mucosas_ref": d["mucosas_ref"],
        "ecc_ideal": ECC_IDEAL_PADRAO,
        "fonte_bibliografica": (
            f"Pesquisa automática por IA ({ROTULO_FAIXA[faixa]}), PENDENTE de validação veterinária. "
            f"Fontes oficiais consultadas: {nomes_oficiais}. Referências: {titulos}"
        )[:600],
        "fontes": json.dumps({
            "citadas": d["fontes_citadas"],
            "web": resposta.fontes,
            "oficiais": oficiais,
            "grupo": grupo,
            "faixa_etaria": faixa,
            "idade_pesquisada": idade,
            "confianca": d["confianca"],
            "modelo": f"{resposta.provedor}/{resposta.modelo}",
        }, ensure_ascii=False),
        "observacoes_ia": d["observacoes"],
    }

    novo_id = db.execute(_INSERIR, parametros).scalar()
    db.commit()

    if novo_id is None:
        achada = next((l for l in db.query(Bib).all()
                       if l.especie == especie_perfil and _norm(l.sexo) == _norm(sexo_bib)
                       and _faixa_da_linha(l) == faixa), None)
        return achada, (None if achada else "conflito ao gravar a referência")

    return db.query(Bib).filter(Bib.id == novo_id).first(), None


def promover_rascunho_por_id(db: Session, linha_id: int) -> Optional[int]:
    """
    Chamada ao FINALIZAR a triagem: a linha RASCUNHO usada por este paciente passa a PENDENTE
    (fila de validação do veterinário). Por id, não por raça: funciona também para SRD.
    """
    try:
        promovido = db.execute(
            text("UPDATE biblioteca_parametros_oficiais SET status = 'PENDENTE' "
                 "WHERE id = :i AND status = 'RASCUNHO' RETURNING id"),
            {"i": linha_id},
        ).scalar()
        db.commit()
        return promovido
    except Exception:
        db.rollback()
        logger.exception("Falha ao promover o rascunho da referência")
        return None