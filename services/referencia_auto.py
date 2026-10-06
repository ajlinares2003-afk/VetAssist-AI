"""
services/referencia_auto.py

Quando o animal NÃO tem perfil na biblioteca oficial, a IA (Gemini com pesquisa na web)
procura os parâmetros na literatura e o sistema cadastra o resultado sozinho na
`biblioteca_parametros_oficiais` com status PENDENTE.

Regras de segurança deste fluxo:
  - Só aceita resposta ANCORADA em fontes reais da web (sem fontes = descartada).
  - Confiança "baixa" ou literatura insuficiente = nada é cadastrado.
  - O registro nasce como RASCUNHO: aparece na tela para o enfermeiro, mas NÃO entra na
    biblioteca ainda. Ao FINALIZAR a triagem ele vira PENDENTE (promover_rascunho) e só
    então o veterinário o valida (routers/referencias_admin.py).
  - Nunca cria perfil para raça genérica (SRD): não dá para pesquisar "SRD".
  - Reaproveita o perfil existente (mesmo texto de `especie`) quando só falta o sexo,
    para não duplicar perfis e quebrar a busca.
"""
import json
import logging
import re
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.biblioteca_referencia import (
    GENERICAS, _montar_descritores, _norm, _partes, _sexo_chave,
)
from services.ia_service import IAIndisponivelError, gerar_json

logger = logging.getLogger("vetassist.referencia_auto")

ECC_IDEAL_PADRAO = "5 (Escala 1 a 9)"  # regra da clínica

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
    numero = float(str(valor).replace(",", ".").strip())
    return int(round(numero)) if inteiro else round(numero, 2)


def validar_pesquisa(dados: dict) -> dict:
    """Normaliza e valida o JSON da pesquisa. Levanta ValueError se não servir."""
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
    saida["fontes_citadas"] = [f for f in citadas if isinstance(f, dict)][:8]
    saida["confianca"] = confianca
    saida["observacoes"] = str(dados.get("observacoes") or "").strip()[:1000]
    return saida


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
    partes = [p.strip() for p in re.split(r"\s+-\s+", str(raca or "")) if p.strip()]
    if not partes or _norm(partes[0]) in GENERICAS:
        return None
    return f"{partes[0]} ({' - '.join(partes[1:])})" if len(partes) > 1 else partes[0]


def _perfil_existente(linhas, raca):
    """Descritor que já responde por essa raça (para só acrescentar o sexo que falta)."""
    partes_raca = {p for p in _partes(raca) if p not in GENERICAS}
    return [d for d in _montar_descritores(linhas) if partes_raca & (d.aliases | d.exemplos)]


def _montar_prompt(nome, sexo, porte, sub_especie, especie) -> str:
    return (
        "Você é pesquisador de medicina veterinária. USE A PESQUISA NA WEB para encontrar publicações "
        "científicas (artigos revisados por pares, livros-texto de medicina veterinária ou de animais "
        "silvestres/zoológicos, diretrizes de associações veterinárias) com os parâmetros fisiológicos "
        "de referência de:\n\n"
        f"- Perfil: {nome}\n"
        f"- Espécie / sub-espécie informadas no cadastro: {especie or 'não informada'} / {sub_especie or 'não informada'}\n"
        f"- Sexo: {sexo}\n"
        f"- Porte: {porte or 'não informado'}\n"
        "- Faixa etária: animal ADULTO saudável\n\n"
        "REGRAS:\n"
        "- Use SOMENTE valores encontrados nas fontes. NÃO extrapole de gato ou cão doméstico.\n"
        "- 'repouso' = animal calmo, sem estresse; 'clinica' = durante o atendimento/manejo.\n"
        "- Se houver pouca literatura, ou as fontes não trouxerem os parâmetros, "
        "responda com \"confianca\": \"baixa\" (nesse caso nada será cadastrado).\n"
        "- Números com ponto decimal. FC em bpm, FR em ir/min, temperatura em °C, peso em kg.\n"
        "- O campo tpc_ref traz SOMENTE o tempo de preenchimento capilar em segundos.\n"
        "- Cite cada publicação usada em fontes_citadas, com título, autores, ano e URL (se houver).\n\n"
        "Responda SOMENTE com um objeto JSON puro, exatamente com estas chaves:\n"
        "{\n"
        "  \"classe_animal\": \"Mamífero | Ave | Réptil | ...\",\n"
        "  \"grupo\": \"ex.: Felídeos Silvestres\",\n"
        "  \"nome_cientifico\": \"Gênero espécie\",\n"
        "  \"peso_min\": 0, \"peso_max\": 0,\n"
        "  \"temp_repouso_min\": 0, \"temp_repouso_max\": 0, \"temp_clinica_min\": 0, \"temp_clinica_max\": 0,\n"
        "  \"fc_repouso_min\": 0, \"fc_repouso_max\": 0, \"fc_clinica_min\": 0, \"fc_clinica_max\": 0,\n"
        "  \"fr_repouso_min\": 0, \"fr_repouso_max\": 0, \"fr_clinica_min\": 0, \"fr_clinica_max\": 0,\n"
        "  \"tpc_ref\": \"Até X segundos\",\n"
        "  \"mucosas_ref\": \"descrição curta\",\n"
        "  \"fontes_citadas\": [{\"titulo\": \"\", \"autores\": \"\", \"ano\": \"\", \"url\": \"\"}],\n"
        "  \"confianca\": \"alta | media | baixa\",\n"
        "  \"observacoes\": \"limitações dos dados, em 1 ou 2 frases\"\n"
        "}"
    )


_INSERIR = text("""
    INSERT INTO biblioteca_parametros_oficiais
      (classe_animal, grupo, especie, nome_cientifico, sexo, peso_min, peso_max,
       temp_repouso_min, temp_repouso_max, temp_clinica_min, temp_clinica_max,
       fc_repouso_min, fc_repouso_max, fc_clinica_min, fc_clinica_max,
       fr_repouso_min, fr_repouso_max, fr_clinica_min, fr_clinica_max,
       tpc_ref, mucosas_ref, ecc_ideal, fonte_bibliografica,
       status, origem, fontes, observacoes_ia)
    VALUES
      (:classe_animal, :grupo, :especie, :nome_cientifico, :sexo, :peso_min, :peso_max,
       :temp_repouso_min, :temp_repouso_max, :temp_clinica_min, :temp_clinica_max,
       :fc_repouso_min, :fc_repouso_max, :fc_clinica_min, :fc_clinica_max,
       :fr_repouso_min, :fr_repouso_max, :fr_clinica_min, :fr_clinica_max,
       :tpc_ref, :mucosas_ref, :ecc_ideal, :fonte_bibliografica,
       'RASCUNHO', 'IA', CAST(:fontes AS jsonb), :observacoes_ia)
    ON CONFLICT (especie, sexo) DO NOTHING
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


def pesquisar_e_registrar(db: Session, *, especie, sub_especie, raca, sexo, porte):
    """
    Pesquisa na literatura e guarda o resultado como RASCUNHO (reaproveita o rascunho
    existente do mesmo perfil/sexo, sem nova chamada à IA).
    Devolve (linha, None) em caso de sucesso, ou (None, motivo) quando não foi possível
    (raça genérica, Gemini indisponível, sem fonte, literatura fraca, erro de banco).
    O motivo é mostrado na tela para facilitar o diagnóstico. Nunca levanta exceção.
    """
    try:
        return _pesquisar_e_registrar(db, especie, sub_especie, raca, sexo, porte)
    except Exception as exc:
        db.rollback()
        logger.exception("Falha na pesquisa automática de referência")
        return None, (f"erro ao cadastrar a referência ({type(exc).__name__}). "
                      "Confirme se a migração da biblioteca foi executada.")


def _pesquisar_e_registrar(db, especie, sub_especie, raca, sexo, porte):
    from models.biblioteca_oficial import BibliotecaParametrosOficiais as Bib

    nome = _nome_do_perfil(raca)
    if not nome:
        logger.info("Raça genérica/vazia: não há o que pesquisar automaticamente.")
        return None, "raça genérica ou vazia (SRD): é preciso um perfil curado para esse tipo de animal."

    sexo_bib = _sexo_da_biblioteca(sexo)
    linhas = db.query(Bib).order_by(Bib.id).all()

    # Já existe perfil para essa raça? Reaproveita o MESMO texto de `especie`.
    existentes = _perfil_existente(linhas, raca)
    if len(existentes) > 1:
        logger.warning("Raça %r casa com mais de um perfil; não cadastrar.", raca)
        return None, "a raça casa com mais de um perfil da biblioteca; revise os perfis duplicados."
    base = existentes[0].linhas[0] if existentes else None
    especie_perfil = base.especie if base else nome

    # Concorrência: alguém pode já ter cadastrado este perfil+sexo.
    ja_existe = next((l for l in linhas
                      if l.especie == especie_perfil and _norm(l.sexo) == _norm(sexo_bib)), None)
    if ja_existe:
        return ja_existe, None

    try:
        resposta = gerar_json(
            db,
            _montar_prompt(especie_perfil, sexo_bib, porte, sub_especie, especie),
            validar=validar_pesquisa,
            pesquisa_web=True,
        )
    except IAIndisponivelError as exc:
        logger.warning("Pesquisa automática sem resultado: %s | %s", exc, exc.tentativas)
        if exc.tentativas:
            return None, f"a pesquisa não produziu resultado aceitável ({exc.tentativas[0][:450]})"
        return None, str(exc)

    d = resposta.dados
    titulos = "; ".join(str(f.get("titulo"))[:120] for f in d["fontes_citadas"][:3] if f.get("titulo"))
    parametros = {
        **{campo: d[campo] for campo, _ in CAMPOS_NUMERICOS},
        "classe_animal": getattr(base, "classe_animal", None) or d["classe_animal"],
        "grupo": getattr(base, "grupo", None) or d["grupo"],
        "nome_cientifico": getattr(base, "nome_cientifico", None) or d["nome_cientifico"],
        "especie": especie_perfil,
        "sexo": sexo_bib,
        "tpc_ref": d["tpc_ref"],
        "mucosas_ref": d["mucosas_ref"],
        "ecc_ideal": ECC_IDEAL_PADRAO,
        "fonte_bibliografica": (f"Pesquisa automática por IA, PENDENTE de validação veterinária. "
                                f"Fontes: {titulos}")[:600],
        "fontes": json.dumps({"citadas": d["fontes_citadas"], "web": resposta.fontes,
                              "confianca": d["confianca"], "modelo": f"{resposta.provedor}/{resposta.modelo}"},
                             ensure_ascii=False),
        "observacoes_ia": d["observacoes"],
    }
    novo_id = db.execute(_INSERIR, parametros).scalar()
    db.commit()

    if novo_id is None:  # perdeu a corrida: outro processo inseriu primeiro
        achada = next((l for l in db.query(Bib).all()
                       if l.especie == especie_perfil and _norm(l.sexo) == _norm(sexo_bib)), None)
        return achada, (None if achada else "conflito ao gravar a referência")
    return db.query(Bib).filter(Bib.id == novo_id).first(), None


def promover_rascunho(db: Session, *, raca, sexo) -> Optional[int]:
    """
    Chamada ao FINALIZAR a triagem: o RASCUNHO do perfil/sexo do animal passa a PENDENTE
    (entra na biblioteca e na fila de validação do veterinário). Devolve o id promovido
    ou None. Nunca levanta exceção: uma falha aqui não pode impedir salvar a triagem.
    """
    try:
        if _nome_do_perfil(raca) is None:  # raça genérica não gera perfil automático
            return None
        from models.biblioteca_oficial import BibliotecaParametrosOficiais as Bib
        linhas = db.query(Bib).order_by(Bib.id).all()
        existentes = _perfil_existente(linhas, raca)
        if len(existentes) != 1:
            return None
        promovido = db.execute(
            text("UPDATE biblioteca_parametros_oficiais SET status = 'PENDENTE' "
                 "WHERE especie = :e AND sexo = :s AND status = 'RASCUNHO' RETURNING id"),
            {"e": existentes[0].linhas[0].especie, "s": _sexo_da_biblioteca(sexo)},
        ).scalar()
        db.commit()
        return promovido
    except Exception:
        db.rollback()
        logger.exception("Falha ao promover o rascunho da referência")
        return None