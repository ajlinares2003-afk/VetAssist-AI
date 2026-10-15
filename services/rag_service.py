"""
rag_service.py — RAG nativo do VetAssist AI (PostgreSQL + pgvector + Gemini).

Contém:
  1. Modelo SQLAlchemy `DocumentoVeterinario` (tabela documentos_veterinarios)
  2. gerar_embedding()            -> embeddings via google-genai (cliente em cache)
  3. construir_prompt_com_rag()   -> busca L2 + montagem do prompt com guarda-corpo
  4. Ingestão de PDFs (chunking por frases) + rota FastAPI

Pré-requisitos:
  pip install pgvector pdfplumber python-multipart
  SQL (uma vez, como superusuário/owner do banco):
      CREATE EXTENSION IF NOT EXISTS vector;
      CREATE INDEX IF NOT EXISTS ix_documentos_vet_embedding
          ON documentos_veterinarios USING hnsw (embedding vector_l2_ops);
"""
from __future__ import annotations

import logging
import math
import os
import re
import time
from dataclasses import dataclass
from typing import BinaryIO

import pdfplumber
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from google.genai import types
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Integer, String, Text, delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

# --- Ajustado para a estrutura do seu projeto ---------------------------------
from database.database import Base, get_db           # Base declarativa e sessão
from services.ia_service import (
    _obter_cliente_gemini,
    _ler_configuracoes,
    SLOTS_ORDEM,
    _chave_do_slot,
)
from services.security import exigir_perfil
# -----------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# Configuração -----------------------------------------------------------------
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_DIM = 768
EMBEDDING_TIMEOUT = 30           # repassado à fábrica
EMBEDDING_TENTATIVAS = 4         # tentativas em erros transitórios (limite de taxa, indisponibilidade)
TOP_K = 4                        # trechos recuperados por consulta
CHUNK_MAX_CHARS = 1000
CHUNK_OVERLAP_SENTENCES = 1
EMBED_BATCH_COMMIT = 20          # commit a cada N chunks
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_TITULO_CHARS = 255

# Uso dentro de outras rotas (ex.: Copiloto Clínico): a busca não pode atrasar nem derrubar a rota.
RAG_TIMEOUT_CONSULTA = int(os.getenv("RAG_TIMEOUT_CONSULTA", "10"))


def _ler_distancia_maxima() -> float | None:
    """Distância L2 máxima aceita (vetores normalizados: 0 = idêntico, 1,41 = sem relação).
    Vazio = sem corte. Calibre olhando as distâncias registradas em log por referencias_para_prompt."""
    bruto = os.getenv("RAG_DISTANCIA_MAXIMA", "").strip().replace(",", ".")
    try:
        return float(bruto) if bruto else None
    except ValueError:
        return None


RAG_DISTANCIA_MAXIMA = _ler_distancia_maxima()

_MARCAS_TRANSITORIAS = ("429", "resource_exhausted", "503", "unavailable", "timed out", "timeout")


# Exceções ---------------------------------------------------------------------
class RagError(Exception):
    """Erro base do módulo RAG."""


class EmbeddingError(RagError):
    """Falha ao gerar embedding."""


class IngestaoError(RagError):
    """Falha ao ler/fatiar/gravar um documento."""


# 1. Modelo --------------------------------------------------------------------
class DocumentoVeterinario(Base):
    __tablename__ = "documentos_veterinarios"

    id = Column(Integer, primary_key=True, index=True)
    titulo = Column(String(MAX_TITULO_CHARS), nullable=False)
    conteudo = Column(Text, nullable=False)
    embedding = Column(Vector(EMBEDDING_DIM), nullable=False)


# 2. Embeddings ----------------------------------------------------------------
def gerar_embedding(
    texto: str,
    api_key: str,
    tipo_tarefa: str = "RETRIEVAL_DOCUMENT",
    tentativas: int = EMBEDDING_TENTATIVAS,
    timeout: int = EMBEDDING_TIMEOUT,
) -> list[float]:
    """
    Gera o embedding (768 dims, normalizado) de `texto` reutilizando o cliente Gemini em cache.
    `tentativas` e `timeout` podem ser reduzidos por rotas interativas (ver referencias_para_prompt).

    A normalização é necessária porque, com output_dimensionality menor que o
    tamanho nativo do modelo, o vetor não vem normalizado e a distância L2
    ficaria sensível à magnitude.
    """
    texto = (texto or "").strip()
    if not texto:
        raise EmbeddingError("Texto vazio: nada para vetorizar.")
    if not api_key:
        raise EmbeddingError("Chave de API do Gemini ausente.")

    tentativas = max(1, tentativas)
    resposta = None
    for tentativa in range(1, tentativas + 1):
        try:
            cliente = _obter_cliente_gemini(api_key, timeout)
            resposta = cliente.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=texto,
                config=types.EmbedContentConfig(
                    task_type=tipo_tarefa,
                    output_dimensionality=EMBEDDING_DIM,
                ),
            )
            break
        except Exception as exc:
            transitorio = any(m in str(exc).lower() for m in _MARCAS_TRANSITORIAS)
            if transitorio and tentativa < tentativas:
                espera = 2 ** tentativa
                logger.warning(
                    "Erro transitório no embedding (tentativa %d/%d); nova tentativa em %ds: %s",
                    tentativa, tentativas, espera, exc,
                )
                time.sleep(espera)
                continue
            logger.exception("Falha na chamada de embedding")
            raise EmbeddingError(f"Falha ao gerar embedding: {exc}") from exc

    if resposta is None or not resposta.embeddings or not resposta.embeddings[0].values:
        raise EmbeddingError("A API retornou uma resposta sem embeddings.")

    valores = list(resposta.embeddings[0].values)
    if len(valores) != EMBEDDING_DIM:
        raise EmbeddingError(
            f"Dimensão inesperada: {len(valores)} (esperado {EMBEDDING_DIM})."
        )

    norma = math.sqrt(sum(v * v for v in valores))
    if norma == 0:
        raise EmbeddingError("Embedding inválido (vetor nulo).")
    return [v / norma for v in valores]


# 3. RAG -----------------------------------------------------------------------
REGRAS_SISTEMA = """\
Você é o VetAssist AI, copiloto clínico de apoio a médicos veterinários.
Responda em português do Brasil, de forma técnica e objetiva.

REGRAS OBRIGATÓRIAS:
1. Baseie-se prioritariamente nas REFERÊNCIAS fornecidas. Se elas não cobrirem o \
caso, diga isso explicitamente e deixe claro o que é conhecimento geral.
2. É PROIBIDO gerar objetos JSON, tabelas estruturadas ou qualquer formato \
legível por máquina destinado ao preenchimento automático de receituários, \
especialmente de medicamentos de controle especial.
3. Sugestões farmacológicas (fármaco, dose, via, frequência) devem aparecer \
APENAS em texto livre dentro do parecer clínico, como sugestão sujeita à \
revisão e prescrição do médico veterinário responsável.
4. Nunca afirme diagnóstico definitivo; apresente diagnósticos diferenciais e \
condutas como apoio à decisão clínica.
"""


def buscar_referencias_com_distancia(
    db: Session, embedding_consulta: list[float], limite: int = TOP_K
) -> list[tuple[DocumentoVeterinario, float]]:
    """Busca os `limite` trechos mais próximos pela distância L2 e devolve (trecho, distância)."""
    try:
        distancia = DocumentoVeterinario.embedding.l2_distance(embedding_consulta)
        stmt = (
            select(DocumentoVeterinario, distancia.label("distancia"))
            .order_by(distancia)
            .limit(limite)
        )
        return [(doc, float(dist)) for doc, dist in db.execute(stmt).all()]
    except SQLAlchemyError as exc:
        logger.exception("Falha na busca vetorial")
        raise RagError("Falha ao consultar a base de literatura.") from exc


def buscar_referencias(
    db: Session, embedding_consulta: list[float], limite: int = TOP_K
) -> list[DocumentoVeterinario]:
    """Busca os `limite` trechos mais próximos pela distância L2."""
    return [doc for doc, _ in buscar_referencias_com_distancia(db, embedding_consulta, limite)]


def _filtrar_por_distancia(
    pares: list[tuple[DocumentoVeterinario, float]],
) -> list[tuple[DocumentoVeterinario, float]]:
    """Descarta trechos distantes demais (só se RAG_DISTANCIA_MAXIMA estiver definida)."""
    if RAG_DISTANCIA_MAXIMA is None:
        return pares
    return [(doc, dist) for doc, dist in pares if dist <= RAG_DISTANCIA_MAXIMA]


def _formatar_referencias(trechos: list[DocumentoVeterinario]) -> str:
    """Numera os trechos e informa a obra de origem de cada um."""
    return "\n\n".join(
        f"[{i}] ({t.titulo})\n{t.conteudo}" for i, t in enumerate(trechos, start=1)
    )


def construir_prompt_com_rag(db: Session, api_key: str, pergunta_clinica: str) -> str:
    """Vetoriza a pergunta, recupera os trechos mais relevantes e devolve o prompt final."""
    pergunta_clinica = (pergunta_clinica or "").strip()
    if not pergunta_clinica:
        raise RagError("A pergunta clínica não pode ser vazia.")

    vetor = gerar_embedding(pergunta_clinica, api_key, tipo_tarefa="RETRIEVAL_QUERY")
    pares = _filtrar_por_distancia(buscar_referencias_com_distancia(db, vetor))
    referencias = (
        _formatar_referencias([doc for doc, _ in pares])
        or "(Nenhuma referência encontrada na base de literatura.)"
    )

    return (
        f"{REGRAS_SISTEMA}\n"
        f"=== REFERÊNCIAS DA LITERATURA ===\n{referencias}\n\n"
        f"=== CASO CLÍNICO ===\n{pergunta_clinica}\n\n"
        f"=== PARECER CLÍNICO (texto livre; cite as referências como [n] quando usadas) ==="
    )


def referencias_para_prompt(db: Session, consulta_clinica: str, limite: int = TOP_K) -> str:
    """
    Referências já formatadas para acrescentar ao prompt de OUTRAS rotas (ex.: Copiloto Clínico).

    À prova de falhas: nunca levanta exceção e devolve "" se a base estiver vazia, a chave do
    Gemini faltar, a API demorar ou o banco falhar; quem chama segue sem referências.
    Usa 1 tentativa e timeout curto para não atrasar a rota principal.
    """
    texto = (consulta_clinica or "").strip()[:2000]
    if not texto:
        return ""
    try:
        vetor = gerar_embedding(
            texto, _obter_api_key_gemini(db), "RETRIEVAL_QUERY",
            tentativas=1, timeout=RAG_TIMEOUT_CONSULTA,
        )
        pares = _filtrar_por_distancia(buscar_referencias_com_distancia(db, vetor, limite))
    except Exception as exc:  # noqa: BLE001 — uma falha do RAG não pode derrubar a rota que o usa
        logger.warning("RAG indisponível; seguindo sem referências: %s", exc)
        try:
            db.rollback()  # uma falha SQL deixa a sessão inutilizável para o resto da rota
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao desfazer a transação após erro do RAG")
        return ""

    logger.info(
        "RAG: %d referência(s) recuperada(s) (distâncias: %s)",
        len(pares), ", ".join(f"{dist:.3f}" for _, dist in pares) or "-",
    )
    return _formatar_referencias([doc for doc, _ in pares])


# 4. Ingestão ------------------------------------------------------------------
_SENTENCA = re.compile(r"(?<=[.!?])\s+(?=[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ0-9])")


def extrair_texto_pdf(arquivo: str | BinaryIO) -> str:
    """Extrai o texto de todas as páginas do PDF."""
    try:
        with pdfplumber.open(arquivo) as pdf:
            paginas = [(p.extract_text() or "") for p in pdf.pages]
    except Exception as exc:
        raise IngestaoError(f"PDF ilegível ou corrompido: {exc}") from exc

    texto = "\n".join(paginas)
    texto = re.sub(r"-\n(?=[a-zà-ú])", "", texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\s*\n\s*", " ", texto).strip()
    if len(texto) < 100:
        raise IngestaoError(
            "Quase nenhum texto extraído. O PDF pode ser escaneado (exige OCR)."
        )
    return texto


def _quebrar_texto_longo(texto: str, max_chars: int) -> list[str]:
    """Divide, por palavras, um 'trecho' sem pontuação maior que max_chars (comum em tabelas e bulários)."""
    partes: list[str] = []
    atual = ""
    for palavra in texto.split():
        if atual and len(atual) + 1 + len(palavra) > max_chars:
            partes.append(atual)
            atual = palavra
        else:
            atual = f"{atual} {palavra}".strip()
    if atual:
        partes.append(atual)
    return partes


def fatiar_em_chunks(
    texto: str,
    max_chars: int = CHUNK_MAX_CHARS,
    overlap_sentencas: int = CHUNK_OVERLAP_SENTENCES,
) -> list[str]:
    """Agrupa frases inteiras até ~max_chars com sobreposição."""
    sentencas: list[str] = []
    for bruta in _SENTENCA.split(texto):
        bruta = bruta.strip()
        if not bruta:
            continue
        if len(bruta) > max_chars:
            sentencas.extend(_quebrar_texto_longo(bruta, max_chars))
        else:
            sentencas.append(bruta)

    chunks: list[str] = []
    atual: list[str] = []
    tamanho = 0

    for sentenca in sentencas:
        if atual and tamanho + len(sentenca) + 1 > max_chars:
            chunks.append(" ".join(atual))
            atual = atual[-overlap_sentencas:] if overlap_sentencas else []
            # a sobreposição só é mantida se couber com a próxima frase no limite
            while atual and sum(len(s) + 1 for s in atual) + len(sentenca) + 1 > max_chars:
                atual.pop(0)
            tamanho = sum(len(s) + 1 for s in atual)
        atual.append(sentenca)
        tamanho += len(sentenca) + 1

    if atual:
        chunks.append(" ".join(atual))
    return chunks


# --- Fatiamento por fármaco (bulários em formato de tabela) -----------------------
_GRUPOS_BULARIO = re.compile(
    r"\b(ANTI-INFLAMAT[ÓO]RIOS N[ÃA]O ESTEROIDAIS E ANALG[ÉE]SICOS"
    r"|ANTI-INFLAMAT[ÓO]RIOS ESTEROIDAIS"
    r"|OUTROS PRINC[ÍI]PIOS ATIVOS"
    r"|ANTIMICROBIANOS|ANTIF[ÚU]NGICOS|ANTIPARASIT[ÁA]RIOS?|ANOTAÇ[ÕO]ES)\b"
)
_SENTINELA_GRUPO = re.compile(r"^§(\d+)§$")
_CODIGO_ESPECIE = re.compile(r"^[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ]{3}:$")  # AVE:, FER:, ROE: ...
_INICIO_DESCRICAO = re.compile(r"^[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-zà-ú]")
_ABREV_DOSE = {"VO", "SID", "BID", "TID", "QID", "UI", "IM", "IV", "SC"}
MIN_FARMACOS = 3


def _eh_maiuscula(tok: str) -> bool:
    """Palavra de nome de fármaco: tudo em maiúsculas, não é sigla de via/frequência."""
    if tok == "+":
        return True
    nucleo = tok.strip("()+,.;")
    if not nucleo or tok.endswith(":") or not any(c.isalpha() for c in nucleo):
        return False
    if nucleo != nucleo.upper():
        return False
    return not all(p in _ABREV_DOSE for p in nucleo.split("/"))


def _comeca_descricao(tok: str) -> bool:
    return bool(_INICIO_DESCRICAO.match(tok) or _CODIGO_ESPECIE.match(tok) or tok == "(continuação)")


def _montar_chunks_farmaco(nome: str, grupo: str | None, linhas: list[str], max_chars: int) -> list[str]:
    """Junta as linhas (descrição + uma por espécie) em chunks que SEMPRE começam pelo nome."""
    def cabecalho(parte: int) -> str:
        titulo = nome if parte == 0 else f"{nome} (continuação)"
        return f"{titulo}\nGrupo: {grupo}" if grupo else titulo

    # linhas maiores que o limite (raro) são quebradas por palavras
    linhas_ok: list[str] = []
    folga = max_chars - len(cabecalho(1)) - 1
    for linha in linhas:
        linhas_ok.extend(_quebrar_texto_longo(linha, folga) if len(linha) > folga else [linha])

    chunks: list[str] = []
    atual: list[str] = []
    for linha in linhas_ok:
        candidato = "\n".join([cabecalho(len(chunks)), *atual, linha])
        if atual and len(candidato) > max_chars:
            chunks.append("\n".join([cabecalho(len(chunks)), *atual]))
            atual = []
        atual.append(linha)
    if atual or not chunks:
        chunks.append("\n".join([cabecalho(len(chunks)), *atual]))
    return chunks


def fatiar_por_farmaco(texto: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:
    """Um (ou mais, se longo) chunk por fármaco, sempre iniciado pelo nome do medicamento.

    Pensado para bulários em tabela: NOME EM MAIÚSCULAS + classe + linhas 'AVE: ...', 'FER: ...'.
    Cabeçalhos de grupo repetidos a cada página (ANTIMICROBIANOS...) são removidos e viram o
    campo 'Grupo'; entradas '(continuação)' são fundidas na entrada original.
    """
    texto = re.sub(r"\s+:", ":", texto)

    grupos: list[str] = []

    def _sentinela(m: re.Match[str]) -> str:
        grupos.append(m.group(1))
        return f" §{len(grupos) - 1}§ "

    texto = _GRUPOS_BULARIO.sub(_sentinela, texto)
    if not grupos:
        raise IngestaoError("Cabeçalhos de grupo do bulário não encontrados; use a estratégia 'frases'.")
    # tudo antes do primeiro cabeçalho é capa/legenda
    texto = texto[texto.index("§0§") :]

    tokens = texto.split()
    n = len(tokens)

    def proximo_real(j: int) -> int:
        while j < n and _SENTINELA_GRUPO.match(tokens[j]):
            j += 1
        return j

    # 1) localizar os inícios de entrada: (posição do 1º token, nome, posição após o nome, grupo)
    inicios: list[tuple[int, str, int, str | None]] = []
    grupo_atual: str | None = None
    i = 0
    while i < n:
        m = _SENTINELA_GRUPO.match(tokens[i])
        if m:
            grupo_atual = grupos[int(m.group(1))]
            i += 1
            continue
        if _eh_maiuscula(tokens[i]) and tokens[i] != "+":
            j = i
            while j < n and _eh_maiuscula(tokens[j]):
                j += 1
            fim = j
            while fim > i and tokens[fim - 1] == "+":
                fim -= 1
            seguinte = proximo_real(j)
            if fim > i and seguinte < n and _comeca_descricao(tokens[seguinte]):
                inicios.append((i, " ".join(tokens[i:fim]).strip("()"), fim, grupo_atual))
            i = j
        else:
            i += 1

    # 2) montar as entradas, fundindo as '(continuação)'
    entradas: list[dict] = []
    for k, (pos, nome, apos_nome, grupo) in enumerate(inicios):
        limite = inicios[k + 1][0] if k + 1 < len(inicios) else n
        corpo = [t for t in tokens[apos_nome:limite] if not _SENTINELA_GRUPO.match(t)]
        continuacao = bool(corpo) and corpo[0] == "(continuação)"
        if continuacao and entradas and entradas[-1]["nome"] == nome:
            corpo = corpo[1:]
            # remove a descrição repetida no topo da continuação, se idêntica à original
            cod = next((x for x, t in enumerate(corpo) if _CODIGO_ESPECIE.match(t)), len(corpo))
            repetida = " ".join(corpo[:cod])
            if repetida and entradas[-1]["corpo"].startswith(repetida):
                corpo = corpo[cod:]
            entradas[-1]["corpo"] += " " + " ".join(corpo)
        else:
            if continuacao:
                corpo = corpo[1:]
            entradas.append({"nome": nome, "grupo": grupo, "corpo": " ".join(corpo)})

    if len(entradas) < MIN_FARMACOS:
        raise IngestaoError(
            f"Só {len(entradas)} fármaco(s) reconhecido(s); o PDF não parece um bulário. "
            "Use a estratégia 'frases'."
        )

    # 3) cada entrada -> linhas (descrição + uma linha por espécie) -> chunks
    chunks: list[str] = []
    for e in entradas:
        partes = re.split(r"\s(?=[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ]{3}:)", e["corpo"].strip())
        linhas = [p.strip() for p in partes if p.strip()]
        chunks.extend(_montar_chunks_farmaco(e["nome"], e["grupo"], linhas, max_chars))
    return chunks


ESTRATEGIAS_FATIAMENTO = ("frases", "farmaco")


@dataclass(frozen=True)
class ResultadoIngestao:
    titulo: str
    chunks_gravados: int
    chunks_total: int


def _limpar_ingestao_parcial(db: Session, titulo: str) -> bool:
    """Remove trechos já gravados de uma ingestão que falhou, para permitir nova tentativa."""
    try:
        db.execute(delete(DocumentoVeterinario).where(DocumentoVeterinario.titulo == titulo))
        db.commit()
        return True
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Não foi possível limpar a ingestão parcial de '%s'", titulo)
        return False


def ingerir_pdf(
    db: Session,
    arquivo: str | BinaryIO,
    titulo: str,
    api_key: str,
    estrategia: str = "frases",
) -> ResultadoIngestao:
    """Lê o PDF, fatia, vetoriza e grava por lotes. Se falhar, desfaz o que foi gravado.

    estrategia: 'frases' (livros/textos corridos) ou 'farmaco' (bulário: um trecho por medicamento).
    """
    if estrategia not in ESTRATEGIAS_FATIAMENTO:
        raise IngestaoError(f"Estratégia inválida. Use uma de: {', '.join(ESTRATEGIAS_FATIAMENTO)}.")
    titulo = (titulo or "").strip()
    if not titulo:
        raise IngestaoError("Título obrigatório.")
    if len(titulo) > MAX_TITULO_CHARS:
        raise IngestaoError(f"Título acima de {MAX_TITULO_CHARS} caracteres.")

    try:
        ja_existe = db.scalar(
            select(DocumentoVeterinario.id).where(DocumentoVeterinario.titulo == titulo).limit(1)
        )
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Falha ao verificar duplicidade de '%s'", titulo)
        raise IngestaoError(f"Falha ao consultar a base de literatura: {exc}") from exc
    if ja_existe:
        raise IngestaoError(f"Já existe literatura ingerida com o título '{titulo}'.")

    texto = extrair_texto_pdf(arquivo)
    chunks = fatiar_por_farmaco(texto) if estrategia == "farmaco" else fatiar_em_chunks(texto)
    if not chunks:
        raise IngestaoError("Nenhum trecho gerado a partir do PDF.")

    gravados = 0
    try:
        for i, trecho in enumerate(chunks, start=1):
            db.add(
                DocumentoVeterinario(
                    titulo=titulo,
                    conteudo=trecho,
                    embedding=gerar_embedding(trecho, api_key, "RETRIEVAL_DOCUMENT"),
                )
            )
            if i % EMBED_BATCH_COMMIT == 0:
                db.commit()
                gravados = i
        db.commit()
        gravados = len(chunks)
    except (EmbeddingError, SQLAlchemyError) as exc:
        db.rollback()
        limpo = _limpar_ingestao_parcial(db, titulo)
        logger.error("Ingestão interrompida em '%s' (%d/%d)", titulo, gravados, len(chunks))
        situacao = (
            "Nada foi mantido no banco; você pode tentar de novo."
            if limpo
            else "Pode haver trechos parciais no banco; remova-os antes de tentar de novo."
        )
        raise IngestaoError(f"Ingestão interrompida: {exc} {situacao}") from exc

    return ResultadoIngestao(titulo, gravados, len(chunks))


# Rota FastAPI -------------------------------------------------------------------
router = APIRouter(prefix="/rag", tags=["RAG"])


def _obter_api_key_gemini(db: Session) -> str:
    """Busca a chave do Gemini em tempo real do banco de dados (Slot Principal)."""
    try:
        config = _ler_configuracoes(db)
        slot_gemini = SLOTS_ORDEM[0]  # CONFIRME: este slot precisa ser o do Gemini
        chave = _chave_do_slot(slot_gemini, config)
    except SQLAlchemyError as exc:
        raise RagError(f"Falha ao ler as configurações de IA: {exc}") from exc
    if not chave:
        raise RagError("Chave do Gemini não configurada no painel de Configurações de IA.")
    return chave


# Mesma proteção usada no restante do sistema (services/security.py): perfil ADMIN no token.
exigir_admin = exigir_perfil(["ADMIN"])


@router.post(
    "/ingerir",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(exigir_admin)],
)
def ingerir_literatura(
    titulo: str = Form(...),
    arquivo: UploadFile = File(...),
    estrategia: str = Form("frases", description="'frases' (livros) ou 'farmaco' (bulário)"),
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    if arquivo.content_type != "application/pdf":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Envie um arquivo PDF.")

    arquivo.file.seek(0, os.SEEK_END)
    if arquivo.file.tell() > MAX_PDF_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "PDF acima de 50 MB.")
    arquivo.file.seek(0)

    try:
        r = ingerir_pdf(db, arquivo.file, titulo, _obter_api_key_gemini(db), estrategia)
    except IngestaoError as exc:
        # 422 literal: o nome antigo da constante foi depreciado no Starlette
        raise HTTPException(422, str(exc)) from exc
    except RagError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return {"titulo": r.titulo, "chunks_gravados": r.chunks_gravados, "chunks_total": r.chunks_total}