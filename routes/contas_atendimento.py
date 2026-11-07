"""
routers/contas_atendimento.py

Conta do atendimento (cobrança) — Fase 1:
  - o veterinário FINALIZA o atendimento: o prontuário trava e a conta é montada automaticamente
    (consulta ou retorno sem cobrança, medicamentos aplicados na clínica, exames com preço cadastrado);
  - a recepção revisa, edita itens, dá desconto, escolhe a forma de pagamento e registra o pagamento
    (lança a receita em transacoes_financeiras e muda a consulta para "Finalizado");
  - o ADMIN mantém a tabela de preços e pode reabrir um atendimento ainda não pago.

Tudo usa SQL direto (sqlalchemy.text), sem depender de novos modelos ORM.
Cirurgia, internação e estoque de insumos ficam para a Fase 2 (a recepção pode lançar itens manuais).
"""
import logging
import re
import unicodedata
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db
from services.security import exigir_perfil

logger = logging.getLogger("vetassist.contas")

router = APIRouter(prefix="/contas", tags=["Contas do atendimento"])

# Valor gravado em transacoes_financeiras.tipo para uma entrada de caixa. Se a tela Financeiro usa outro
# rótulo (ex.: "Receita", "ENTRADA"), é só trocar aqui.
TIPO_RECEITA = "RECEITA"
CATEGORIA_RECEITA = "Atendimento Clínico"

STATUS_AGUARDANDO = "Aguardando Pagamento"
STATUS_FINALIZADO = "Finalizado"
PRAZO_RETORNO_PADRAO = 15
FORMAS_PAGAMENTO = ["Dinheiro", "Pix", "Cartão de débito", "Cartão de crédito", "Transferência", "Isento"]
CATEGORIAS = ["CONSULTA", "RETORNO", "EXAME", "MEDICAMENTO", "PROCEDIMENTO", "INTERNACAO", "CIRURGIA", "INSUMO", "OUTRO"]
UNIDADES = ["un", "aplicacao", "mL", "dose", "dia"]

PERFIS_VET = ["ADMIN", "VETERINARIO"]
PERFIS_CAIXA = ["ADMIN", "RECEPCAO"]
PERFIS_TODOS = ["ADMIN", "VETERINARIO", "RECEPCAO"]


# ---------------------------------------------------------------- utilidades puras
def _d(valor, padrao="0") -> Decimal:
    try:
        return Decimal(str(valor if valor is not None else padrao)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal(padrao).quantize(Decimal("0.01"))


def _norm(texto) -> str:
    if not texto:
        return ""
    sem = unicodedata.normalize("NFKD", str(texto).lower())
    sem = "".join(c for c in sem if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", sem).strip()


def _achar_preco(precos: list, categoria: str, nome: str):
    """Linha de preço ativa da categoria cujo termo (palavras-chave ou nome) aparece no nome do item.
    Em caso de empate vale o termo mais longo (mais específico)."""
    alvo = f" {_norm(nome)} "
    melhor, tam = None, 0
    for p in precos:
        if p["categoria"] != categoria:
            continue
        termos = [t for t in re.split(r"[,;]", p.get("palavras_chave") or "") if t.strip()] or [p["nome"]]
        for t in termos:
            tn = _norm(t)
            if len(tn) >= 3 and f" {tn} " in alvo and len(tn) > tam:
                melhor, tam = p, len(tn)
    return melhor


def _num_br(s) -> Optional[Decimal]:
    try:
        return Decimal(str(s).replace(".", "").replace(",", ".")) if "," in str(s) else Decimal(str(s))
    except Exception:
        return None


def _extrair_aplicacoes(*textos) -> tuple:
    """(n de aplicações, mL totais) lidos da receita ('6 (seis) aplicações de ≈ 1,07 mL (total ≈ 6,4 mL)').
    Se a receita não traz a quantidade, devolve (None, None): a conta usa 1 e avisa para conferir."""
    t = " ".join(str(x or "") for x in textos)
    n = None
    m = re.search(r"(\d+)\s*(?:\([^)]*\))?\s*aplica[cç]", t, re.I)
    if m:
        n = int(m.group(1))
    elif re.search(r"dose\s+[úu]nica", t, re.I):
        n = 1
    ml_total = None
    m = re.search(r"total\s*[≈~]?\s*([\d.,]+)\s*mL", t, re.I)
    if m:
        ml_total = _num_br(m.group(1))
    else:
        m = re.search(r"aplica\w*\s+de\s*[≈~]?\s*([\d.,]+)\s*mL", t, re.I)
        if m and n:
            por = _num_br(m.group(1))
            ml_total = (por * n) if por is not None else None
    return n, ml_total


def _item(categoria, descricao, quantidade, valor_unitario, origem="AUTO", sem_preco=False, nota=None) -> dict:
    q, v = _d(quantidade, "1"), _d(valor_unitario)
    return {"categoria": categoria, "descricao": str(descricao)[:300], "quantidade": q, "valor_unitario": v,
            "valor_total": (q * v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "origem": origem, "sem_preco": bool(sem_preco), "nota": nota}


def _totais(itens: list, desconto) -> tuple:
    subtotal = sum((_d(i["valor_total"]) for i in itens), Decimal("0.00"))
    desc = min(_d(desconto), subtotal)
    return subtotal, desc, max(subtotal - desc, Decimal("0.00"))


# ---------------------------------------------------------------- acesso ao banco
def _perfil_de(usuario) -> str:
    perfil = usuario.get("perfil") if isinstance(usuario, dict) else getattr(usuario, "perfil", "")
    return str(getattr(perfil, "value", perfil) or "").upper()


def _id_de(usuario):
    return usuario.get("id") if isinstance(usuario, dict) else getattr(usuario, "id", None)


def _precos_ativos(db: Session) -> list:
    rows = db.execute(text("select id, categoria, nome, palavras_chave, unidade, valor from public.tabela_precos "
                           "where ativo order by id")).mappings().all()
    return [dict(r) for r in rows]


def _prazo_padrao(db: Session) -> int:
    try:
        v = db.execute(text("select valor from public.configuracoes_sistema where chave='retorno_gratuito_dias' limit 1")).scalar()
        return max(1, int(str(v).strip())) if v is not None else PRAZO_RETORNO_PADRAO
    except Exception:
        return PRAZO_RETORNO_PADRAO


def _consulta(db: Session, consulta_id: int) -> dict:
    row = db.execute(text(
        "select c.id, c.codigo, c.status, c.animal_id, c.data_consulta, c.queixa_principal, c.indicacao_cirurgia, "
        "       a.nome as animal_nome, t.nome as tutor_nome "
        "from public.consulta c left join public.animal a on a.id = c.animal_id "
        "left join public.tutor t on t.id = a.tutor_id where c.id = :id"), {"id": consulta_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Atendimento não encontrado.")
    return dict(row)


def _sugestao_retorno(db: Session, consulta: dict) -> dict:
    """Consulta anterior (finalizada) do MESMO animal ainda dentro do prazo de retorno gratuito."""
    prazo_padrao = _prazo_padrao(db)
    ant = db.execute(text(
        "select c.id, c.codigo, c.data_consulta, c.queixa_principal, c.retorno_dias, c.retorno_da_consulta_id "
        "from public.consulta c where c.animal_id = :a and c.id < :id and c.finalizada_em is not null "
        "order by c.finalizada_em desc limit 1"), {"a": consulta["animal_id"], "id": consulta["id"]}).mappings().first()
    vazio = {"sugerido": False, "prazo_padrao": prazo_padrao, "origem": None}
    if not ant:
        return vazio
    origem = dict(ant)
    if origem.get("retorno_da_consulta_id"):   # a anterior já era retorno: o prazo conta a partir do atendimento original
        org = db.execute(text("select id, codigo, data_consulta, queixa_principal, retorno_dias from public.consulta where id = :i"),
                         {"i": origem["retorno_da_consulta_id"]}).mappings().first()
        if org:
            origem = dict(org)
    prazo = int(origem.get("retorno_dias") or prazo_padrao)
    dias = db.execute(text("select ((now() at time zone 'America/Sao_Paulo')::date - (cast(:d as timestamptz) at time zone 'America/Sao_Paulo')::date)"),
                      {"d": origem["data_consulta"]}).scalar()
    dentro = dias is not None and 0 <= int(dias) <= prazo
    return {"sugerido": bool(dentro), "prazo_padrao": prazo_padrao,
            "origem": {"id": origem["id"], "codigo": origem.get("codigo"),
                       "data": str(origem["data_consulta"])[:10], "queixa": origem.get("queixa_principal"),
                       "dias_desde": int(dias) if dias is not None else None, "prazo_dias": prazo}}


def _montar_itens(db: Session, consulta: dict, retorno_gratuito: bool, origem_codigo: Optional[str]) -> tuple:
    """Itens automáticos da conta + avisos para a recepção."""
    precos = _precos_ativos(db)
    itens, avisos = [], []

    # 1) consulta ou retorno
    if retorno_gratuito:
        itens.append(_item("RETORNO", "Consulta de retorno (sem cobrança)" + (f" — retorno de {origem_codigo}" if origem_codigo else ""),
                           1, 0, nota="Retorno do mesmo problema, dentro do prazo (confirmado pelo veterinário)."))
    else:
        p = next((x for x in precos if x["categoria"] == "CONSULTA"), None)
        valor = _d(p["valor"]) if p else Decimal("0")
        itens.append(_item("CONSULTA", p["nome"] if p else "Consulta clínica", 1, valor,
                           sem_preco=(p is None or valor == 0),
                           nota=None if (p and valor > 0) else "Defina o valor da consulta na Tabela de Preços."))

    # 2) medicamentos aplicados na clínica (última receita salva do atendimento)
    meds = db.execute(text(
        "select ip.medicamento, ip.dosagem, ip.frequencia, ip.duracao, ip.observacoes "
        "from public.item_prescricao ip join public.prescricao p on p.id = ip.prescricao_id "
        "where p.id = (select max(id) from public.prescricao where consulta_id = :c) "
        "  and ip.tipo_uso like 'Uso Cl%' order by ip.id"), {"c": consulta["id"]}).mappings().all()
    vistos = set()
    for m in meds:
        chave = _norm(m["medicamento"])
        if not chave or chave in vistos:
            continue
        vistos.add(chave)
        n, ml_total = _extrair_aplicacoes(m["observacoes"], m["dosagem"])
        p = _achar_preco(precos, "MEDICAMENTO", m["medicamento"])
        if p and p["unidade"] == "mL":
            qtd = ml_total if ml_total is not None else Decimal("1")
            nota = (f"{qtd} mL pela receita" if ml_total is not None else "Volume não informado na receita: conferir a quantidade.")
        else:
            qtd = Decimal(n) if n else Decimal("1")
            nota = (f"{n} aplicação(ões) pela receita" if n else "Quantidade não informada na receita: conferir.")
        valor = _d(p["valor"]) if p else Decimal("0")
        itens.append(_item("MEDICAMENTO", m["medicamento"], qtd, valor, sem_preco=(p is None or valor == 0),
                           nota=nota if (p and valor > 0) else "Sem preço cadastrado: defina o valor ou remova o item."))

    # 3) exames: só entram os que têm preço cadastrado (indica que a clínica os realiza); os demais vão para o aviso
    nomes = db.execute(text(
        "select nome_exame from public.solicitacao_exame where consulta_id = :c "
        "union select nome_exame from public.exame where consulta_id = :c"), {"c": consulta["id"]}).scalars().all()
    vistos_ex, sem_preco = set(), []
    for nome in nomes:
        chave = _norm(nome)
        if not chave or chave in vistos_ex:
            continue
        vistos_ex.add(chave)
        p = _achar_preco(precos, "EXAME", nome)
        valor = _d(p["valor"]) if p else Decimal("0")
        if p and valor > 0:
            itens.append(_item("EXAME", nome, 1, valor, nota="Exame solicitado no atendimento: remova se o tutor fará em outro local."))
        else:
            sem_preco.append(nome)
    if sem_preco:
        avisos.append("Exames solicitados sem preço cadastrado (não cobrados automaticamente): " + "; ".join(sem_preco[:15])
                      + ". Se foram feitos na clínica, lance como item manual.")
    if consulta.get("indicacao_cirurgia"):
        avisos.append("Atendimento com indicação cirúrgica: cirurgia e internação não são cobradas automaticamente nesta versão; lance como item manual.")
    return itens, avisos


def _gravar_itens(db: Session, conta_id: int, itens: list):
    db.execute(text("delete from public.item_conta where conta_id = :c"), {"c": conta_id})
    for i in itens:
        db.execute(text(
            "insert into public.item_conta (conta_id, categoria, descricao, quantidade, valor_unitario, valor_total, origem, sem_preco, nota) "
            "values (:c, :cat, :d, :q, :vu, :vt, :o, :sp, :n)"),
            {"c": conta_id, "cat": i["categoria"], "d": i["descricao"], "q": i["quantidade"], "vu": i["valor_unitario"],
             "vt": i["valor_total"], "o": i["origem"], "sp": i["sem_preco"], "n": i.get("nota")})


def _detalhe(db: Session, conta_id: int) -> dict:
    c = db.execute(text(
        "select ca.*, co.codigo as consulta_codigo, co.queixa_principal, co.animal_id, a.nome as animal_nome, a.especie, t.nome as tutor_nome, "
        "       t.telefone as tutor_telefone, u.nome as veterinario_nome "
        "from public.conta_atendimento ca join public.consulta co on co.id = ca.consulta_id "
        "left join public.animal a on a.id = co.animal_id left join public.tutor t on t.id = a.tutor_id "
        "left join public.usuario u on u.id = ca.finalizada_por where ca.id = :i"), {"i": conta_id}).mappings().first()
    if not c:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    itens = db.execute(text("select id, categoria, descricao, quantidade, valor_unitario, valor_total, origem, sem_preco, nota "
                            "from public.item_conta where conta_id = :i order by id"), {"i": conta_id}).mappings().all()
    out = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in dict(c).items()}
    out["itens"] = [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in dict(i).items()} for i in itens]
    for k in ("subtotal", "desconto", "total"):
        out[k] = float(c[k])
    return out


# ---------------------------------------------------------------- esquemas
class FinalizarIn(BaseModel):
    retorno_gratuito: bool = False
    retorno_consulta_origem_id: Optional[int] = None
    retorno_motivo: Optional[str] = Field(None, max_length=500)
    retorno_dias: Optional[int] = Field(None, ge=1, le=365)   # prazo de retorno gratuito DESTE atendimento (vazio = padrão)


class ItemIn(BaseModel):
    categoria: str = "OUTRO"
    descricao: str = Field(..., min_length=1, max_length=300)
    quantidade: float = Field(1, ge=0, le=9999)
    valor_unitario: float = Field(0, ge=0, le=1_000_000)
    origem: str = "MANUAL"
    sem_preco: bool = False
    nota: Optional[str] = Field(None, max_length=300)


class ContaEdicaoIn(BaseModel):
    itens: List[ItemIn] = Field(default_factory=list, max_length=80)
    desconto: float = Field(0, ge=0, le=1_000_000)
    observacoes: Optional[str] = Field(None, max_length=1000)


class PagamentoIn(BaseModel):
    forma_pagamento: Optional[str] = None
    confirmar_itens_sem_valor: bool = False


class PrecoIn(BaseModel):
    categoria: str
    nome: str = Field(..., min_length=2, max_length=200)
    palavras_chave: Optional[str] = Field(None, max_length=300)
    unidade: str = "un"
    valor: float = Field(0, ge=0, le=1_000_000)
    ativo: bool = True


def _validar_preco(p: PrecoIn):
    if p.categoria not in CATEGORIAS:
        raise HTTPException(status_code=422, detail=f"Categoria inválida. Use: {', '.join(CATEGORIAS)}.")
    if p.unidade not in UNIDADES:
        raise HTTPException(status_code=422, detail=f"Unidade inválida. Use: {', '.join(UNIDADES)}.")


# ---------------------------------------------------------------- tabela de preços
@router.get("/precos")
def listar_precos(somente_ativos: bool = False, usuario=Depends(exigir_perfil(PERFIS_TODOS)), db: Session = Depends(get_db)):
    sql = "select id, categoria, nome, palavras_chave, unidade, valor, ativo from public.tabela_precos"
    if somente_ativos:
        sql += " where ativo"
    rows = db.execute(text(sql + " order by categoria, nome")).mappings().all()
    return [{**dict(r), "valor": float(r["valor"])} for r in rows]


@router.post("/precos")
def criar_preco(dados: PrecoIn, usuario=Depends(exigir_perfil(["ADMIN"])), db: Session = Depends(get_db)):
    _validar_preco(dados)
    novo = db.execute(text(
        "insert into public.tabela_precos (categoria, nome, palavras_chave, unidade, valor, ativo) "
        "values (:c, :n, :k, :u, :v, :a) returning id"),
        {"c": dados.categoria, "n": dados.nome.strip(), "k": (dados.palavras_chave or "").strip() or None,
         "u": dados.unidade, "v": _d(dados.valor), "a": dados.ativo}).scalar()
    db.commit()
    return {"id": novo}


@router.put("/precos/{preco_id}")
def atualizar_preco(preco_id: int, dados: PrecoIn, usuario=Depends(exigir_perfil(["ADMIN"])), db: Session = Depends(get_db)):
    _validar_preco(dados)
    r = db.execute(text(
        "update public.tabela_precos set categoria=:c, nome=:n, palavras_chave=:k, unidade=:u, valor=:v, ativo=:a where id=:i"),
        {"c": dados.categoria, "n": dados.nome.strip(), "k": (dados.palavras_chave or "").strip() or None,
         "u": dados.unidade, "v": _d(dados.valor), "a": dados.ativo, "i": preco_id})
    if r.rowcount == 0:
        raise HTTPException(status_code=404, detail="Item de preço não encontrado.")
    db.commit()
    return {"ok": True}


@router.delete("/precos/{preco_id}")
def desativar_preco(preco_id: int, usuario=Depends(exigir_perfil(["ADMIN"])), db: Session = Depends(get_db)):
    """Não apaga: desativa (contas antigas continuam consistentes)."""
    r = db.execute(text("update public.tabela_precos set ativo = false where id = :i"), {"i": preco_id})
    if r.rowcount == 0:
        raise HTTPException(status_code=404, detail="Item de preço não encontrado.")
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- retorno
@router.get("/retorno-sugerido/{consulta_id}")
def retorno_sugerido(consulta_id: int, usuario=Depends(exigir_perfil(PERFIS_VET)), db: Session = Depends(get_db)):
    """Sugere (não decide) se este atendimento é retorno do mesmo animal dentro do prazo gratuito."""
    return _sugestao_retorno(db, _consulta(db, consulta_id))


# ---------------------------------------------------------------- finalizar (veterinário)
@router.post("/finalizar/{consulta_id}")
def finalizar_atendimento(consulta_id: int, dados: FinalizarIn,
                          usuario=Depends(exigir_perfil(PERFIS_VET)), db: Session = Depends(get_db)):
    consulta = _consulta(db, consulta_id)
    if consulta["status"] == STATUS_FINALIZADO:
        raise HTTPException(status_code=409, detail="Este atendimento já foi finalizado e pago.")
    if consulta["status"] == "Aguardando Cirurgia" or consulta.get("indicacao_cirurgia"):
        raise HTTPException(status_code=409, detail="Atendimento com indicação cirúrgica: finalize depois do procedimento "
                                                    "(cirurgia e internação entram na Fase 2).")
    origem_codigo, origem_id = None, None
    if dados.retorno_gratuito:
        origem_id = dados.retorno_consulta_origem_id
        if not origem_id:
            raise HTTPException(status_code=422, detail="Informe de qual atendimento este é o retorno.")
        org = db.execute(text("select id, codigo, animal_id from public.consulta where id = :i"), {"i": origem_id}).mappings().first()
        if not org or org["animal_id"] != consulta["animal_id"] or org["id"] >= consulta["id"]:
            raise HTTPException(status_code=422, detail="O atendimento de origem precisa ser anterior e do mesmo animal.")
        origem_codigo = org["codigo"]

    conta = db.execute(text("select id, status from public.conta_atendimento where consulta_id = :c"), {"c": consulta_id}).mappings().first()
    if conta and conta["status"] == "PAGA":
        raise HTTPException(status_code=409, detail="A conta deste atendimento já foi paga.")

    itens, avisos = _montar_itens(db, consulta, dados.retorno_gratuito, origem_codigo)
    if conta:
        # reabertura/nova finalização antes do pagamento: mantém os itens lançados à mão
        manuais = db.execute(text("select categoria, descricao, quantidade, valor_unitario, valor_total, origem, sem_preco, nota "
                                  "from public.item_conta where conta_id = :c and origem = 'MANUAL'"), {"c": conta["id"]}).mappings().all()
        itens += [dict(m) for m in manuais]
        conta_id = conta["id"]
    else:
        conta_id = db.execute(text(
            "insert into public.conta_atendimento (consulta_id, finalizada_por) values (:c, :u) returning id"),
            {"c": consulta_id, "u": _id_de(usuario)}).scalar()

    desconto = db.execute(text("select desconto from public.conta_atendimento where id = :i"), {"i": conta_id}).scalar() or 0
    subtotal, desc, total = _totais(itens, desconto)
    _gravar_itens(db, conta_id, itens)
    db.execute(text(
        "update public.conta_atendimento set status='AGUARDANDO_PAGAMENTO', retorno_gratuito=:rg, retorno_consulta_origem_id=:ro, "
        "retorno_motivo=:rm, subtotal=:s, desconto=:d, total=:t, avisos=:av, finalizada_por=:u, finalizada_em=now() where id=:i"),
        {"rg": dados.retorno_gratuito, "ro": origem_id, "rm": (dados.retorno_motivo or "").strip() or None,
         "s": subtotal, "d": desc, "t": total, "av": " | ".join(avisos) or None, "u": _id_de(usuario), "i": conta_id})
    db.execute(text(
        "update public.consulta set status = :st, finalizada_em = now(), retorno_dias = :rd, retorno_da_consulta_id = :ro where id = :c"),
        {"st": STATUS_AGUARDANDO, "rd": dados.retorno_dias, "ro": origem_id, "c": consulta_id})
    db.commit()
    logger.info("Atendimento %s finalizado por %s; conta %s total R$ %s", consulta["codigo"], _id_de(usuario), conta_id, total)
    return _detalhe(db, conta_id)


# ---------------------------------------------------------------- caixa (recepção)
@router.get("/pendentes")
def listar_pendentes(status: str = "AGUARDANDO_PAGAMENTO", limite: int = 100,
                     usuario=Depends(exigir_perfil(PERFIS_CAIXA)), db: Session = Depends(get_db)):
    if status not in ("AGUARDANDO_PAGAMENTO", "PAGA"):
        raise HTTPException(status_code=422, detail="Status inválido.")
    rows = db.execute(text(
        "select ca.id, ca.status, ca.total, ca.retorno_gratuito, ca.finalizada_em, ca.paga_em, ca.forma_pagamento, "
        "       co.codigo as consulta_codigo, a.nome as animal_nome, t.nome as tutor_nome "
        "from public.conta_atendimento ca join public.consulta co on co.id = ca.consulta_id "
        "left join public.animal a on a.id = co.animal_id left join public.tutor t on t.id = a.tutor_id "
        "where ca.status = :s order by coalesce(ca.paga_em, ca.finalizada_em) desc limit :l"),
        {"s": status, "l": max(1, min(limite, 300))}).mappings().all()
    return [{**dict(r), "total": float(r["total"])} for r in rows]


@router.get("/por-consulta/{consulta_id}")
def conta_da_consulta(consulta_id: int, usuario=Depends(exigir_perfil(PERFIS_TODOS)), db: Session = Depends(get_db)):
    cid = db.execute(text("select id from public.conta_atendimento where consulta_id = :c"), {"c": consulta_id}).scalar()
    if not cid:
        raise HTTPException(status_code=404, detail="Este atendimento ainda não tem conta.")
    return _detalhe(db, cid)


@router.get("/{conta_id}")
def detalhe_conta(conta_id: int, usuario=Depends(exigir_perfil(PERFIS_CAIXA)), db: Session = Depends(get_db)):
    return _detalhe(db, conta_id)


@router.put("/{conta_id}")
def editar_conta(conta_id: int, dados: ContaEdicaoIn, usuario=Depends(exigir_perfil(PERFIS_CAIXA)), db: Session = Depends(get_db)):
    """Substitui os itens, o desconto e as observações enquanto a conta está aguardando pagamento."""
    conta = db.execute(text("select id, status from public.conta_atendimento where id = :i for update"), {"i": conta_id}).mappings().first()
    if not conta:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    if conta["status"] != "AGUARDANDO_PAGAMENTO":
        raise HTTPException(status_code=409, detail="Só é possível editar contas aguardando pagamento.")
    itens = []
    for i in dados.itens:
        cat = i.categoria if i.categoria in CATEGORIAS else "OUTRO"
        origem = i.origem if i.origem in ("AUTO", "MANUAL") else "MANUAL"
        itens.append(_item(cat, i.descricao.strip(), i.quantidade, i.valor_unitario, origem=origem, sem_preco=i.sem_preco, nota=i.nota))
    subtotal, desc, total = _totais(itens, dados.desconto)
    if _d(dados.desconto) > subtotal:
        raise HTTPException(status_code=422, detail="O desconto não pode ser maior que o subtotal.")
    _gravar_itens(db, conta_id, itens)
    db.execute(text("update public.conta_atendimento set subtotal=:s, desconto=:d, total=:t, observacoes=:o where id=:i"),
               {"s": subtotal, "d": desc, "t": total, "o": (dados.observacoes or "").strip() or None, "i": conta_id})
    db.commit()
    return _detalhe(db, conta_id)


@router.post("/{conta_id}/pagar")
def registrar_pagamento(conta_id: int, dados: PagamentoIn, usuario=Depends(exigir_perfil(PERFIS_CAIXA)), db: Session = Depends(get_db)):
    conta = db.execute(text("select id, status, consulta_id, total, desconto from public.conta_atendimento where id = :i for update"),
                       {"i": conta_id}).mappings().first()
    if not conta:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    if conta["status"] != "AGUARDANDO_PAGAMENTO":
        raise HTTPException(status_code=409, detail="Esta conta não está aguardando pagamento.")

    itens = db.execute(text("select valor_total, valor_unitario, sem_preco from public.item_conta where conta_id = :c"),
                       {"c": conta_id}).mappings().all()
    sem_valor = [i for i in itens if i["sem_preco"] and _d(i["valor_unitario"]) == 0]
    if sem_valor and not dados.confirmar_itens_sem_valor:
        raise HTTPException(status_code=409, detail=f"{len(sem_valor)} item(ns) sem valor definido. Defina o valor, remova o item "
                                                    "ou confirme que será cobrado como cortesia (R$ 0,00).")
    subtotal = sum((_d(i["valor_total"]) for i in itens), Decimal("0.00"))
    total = max(subtotal - min(_d(conta["desconto"]), subtotal), Decimal("0.00"))
    forma = (dados.forma_pagamento or "").strip()
    if total > 0 and forma not in FORMAS_PAGAMENTO:
        raise HTTPException(status_code=422, detail=f"Escolha a forma de pagamento: {', '.join(FORMAS_PAGAMENTO[:-1])}.")

    info = db.execute(text(
        "select co.codigo, a.nome as animal, t.nome as tutor from public.consulta co "
        "left join public.animal a on a.id = co.animal_id left join public.tutor t on t.id = a.tutor_id where co.id = :c"),
        {"c": conta["consulta_id"]}).mappings().first() or {}
    transacao_id = None
    if total > 0:
        descricao = f"{info.get('codigo') or 'Atendimento'} - {info.get('animal') or 'paciente'} (Tutor: {info.get('tutor') or '-'})"[:250]
        transacao_id = db.execute(text(
            "insert into public.transacoes_financeiras (tipo, categoria, descricao, valor, forma_pagamento, data) "
            "values (:t, :c, :d, :v, :f, (now() at time zone 'America/Sao_Paulo')::date) returning id"),
            {"t": TIPO_RECEITA, "c": CATEGORIA_RECEITA, "d": descricao, "v": float(total), "f": forma}).scalar()
    db.execute(text(
        "update public.conta_atendimento set status='PAGA', total=:t, forma_pagamento=:f, paga_em=now(), paga_por=:u, transacao_id=:x where id=:i"),
        {"t": total, "f": forma or "Isento", "u": _id_de(usuario), "x": transacao_id, "i": conta_id})
    db.execute(text("update public.consulta set status = :s where id = :c"), {"s": STATUS_FINALIZADO, "c": conta["consulta_id"]})
    db.commit()
    logger.info("Conta %s paga (R$ %s, %s) por %s", conta_id, total, forma, _id_de(usuario))
    return _detalhe(db, conta_id)


@router.post("/{conta_id}/reabrir")
def reabrir_atendimento(conta_id: int, usuario=Depends(exigir_perfil(["ADMIN"])), db: Session = Depends(get_db)):
    """Desfaz a finalização de uma conta AINDA NÃO PAGA (corrigir prontuário). Contas pagas exigem estorno (Fase 2)."""
    conta = db.execute(text("select id, status, consulta_id from public.conta_atendimento where id = :i for update"),
                       {"i": conta_id}).mappings().first()
    if not conta:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    if conta["status"] == "PAGA":
        raise HTTPException(status_code=409, detail="Conta já paga: o estorno ainda não faz parte desta versão.")
    db.execute(text("delete from public.conta_atendimento where id = :i"), {"i": conta_id})   # itens saem em cascata
    db.execute(text("update public.consulta set status='Em Atendimento', finalizada_em=null where id=:c"), {"c": conta["consulta_id"]})
    db.commit()
    return {"ok": True, "consulta_id": conta["consulta_id"]}