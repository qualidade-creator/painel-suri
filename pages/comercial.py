import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import re
from datetime import datetime, date, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

# =========================================
# CONFIG API
# =========================================

SURI_BASE       = "https://cbm-wap-babysuri-cb89467489-dispe.azurewebsites.net"
SURI_TOKEN      = "e1c8889c-b971-4f7b-b1ed-39af85da92a3"
DEPT_COMERCIAL_ID = "cb89467499"
HEADERS         = {"Authorization": f"Bearer {SURI_TOKEN}"}

# =========================================
# PADRÕES DE ANÁLISE DE CONVERSA
# =========================================

# Frases que indicam que a Suri não soube responder
_BOT_NAO_SOUBE = re.compile(
    r"não (encontrei|tenho|sei|consigo|conheço)|"
    r"nao (encontrei|tenho|sei|consigo|conheco)|"
    r"não (está|estão) (cadastrad|disponível|dispon)|"
    r"não (foi possível|posso te ajudar com isso)|"
    r"preciso verificar|"
    r"vou verificar com|"
    r"não (tenho acesso|localizo|localizei)|"
    r"essa informação não|"
    r"não (encontro|achei) (esse|este|a)|"
    r"desculpe.*não",
    re.IGNORECASE,
)

# Frases que indicam frustração do cliente
_FRUSTRACAO = re.compile(
    r"não entend[ie]|nao entend[ie]|"
    r"quero (falar com|um) (humano|atendente|pessoa)|"
    r"me (passa|transfere|coloca) (para|pra) (uma pessoa|atendente|humano)|"
    r"isso não (resolve|ajuda)|"
    r"já (disse|falei|expliquei)|"
    r"você não (está|ta) (entendendo|me entendendo)|"
    r"cade (o|a) atendente|"
    r"preciso de (alguém|atendente|humano)|"
    r"atendimento humano|"
    r"falar com (alguém|uma pessoa)",
    re.IGNORECASE,
)

# Frases de transferência para humano (SystemMessage)
_TRANSFERENCIA = re.compile(
    r"(transferid[oa]|encaminhand[oa]|redirecionand[oa]|direcionand[oa])"
    r"|(em atendimento|iniciou atendimento|assumiu o atendimento)"
    r"|(aguarde.*atendente|atendente.*assumiu)",
    re.IGNORECASE,
)

# Palavras-chave por tópico de intenção
TOPICOS = {
    "Peças / Reposição":  ["peça", "peca", "peças", "pecas", "reposição", "reposicao", "componente", "kit"],
    "Preço / Orçamento":  ["preço", "preco", "orçamento", "orcamento", "cotação", "cotacao", "valor", "quanto custa", "r$", "tabela", "proposta"],
    "Disponibilidade":    ["tem em estoque", "disponível", "disponivel", "disponibilidade", "tem esse", "tem aquele"],
    "Equipamento/Modelo": ["equipamento", "máquina", "maquina", "modelo", "marca", "ano", "série", "serie", "chassi", "número de série"],
    "Urgência":           ["urgente", "urgência", "urgencia", "rápido", "rapido", "hoje", "agora", "preciso logo", "parado", "quebrado"],
    "Frete / Entrega":    ["frete", "entrega", "prazo", "transportadora", "envio", "despacho", "cep"],
    "Pagamento":          ["pagamento", "boleto", "pix", "cartão", "cartao", "parcelar", "financiar", "à vista", "a vista"],
    "Garantia / Troca":   ["garantia", "troca", "devolução", "devolucao", "defeito", "defeituoso", "não funcionou"],
    "Assistência Técnica":["assistência", "assistencia", "técnico", "tecnico", "manutenção", "manutencao", "conserto", "reparo"],
}

# Extração de códigos de peça (padrões numéricos e alfanuméricos comuns)
_CODIGO_PECA = re.compile(
    r"\b([A-Z]{1,4}\d{3,}|\d{5,}[A-Z]{0,3})\b",
    re.IGNORECASE,
)

# =========================================
# HELPERS
# =========================================

_PALAVRAS_EMPRESA = re.compile(
    r"(LTDA|EIRELI|EPP|(?<!\w)ME(?!\w)|S/?A(?!\w)|CONSORCIO|CONSTRUCAO|MINERACAO"
    r"|SERVICOS|COMERCIO|INDUSTRIA|TRANSPORTES|ENGENHARIA|SOLUCOES|TECNOLOGIA"
    r"|EQUIPAMENTOS|MANUTENCAO|PECAS|DIESEL|GRUPO|BRASIL|NACIONAL|PESADA"
    r"|LOGISTICA|LOCACAO|GESTAO|AMBIENTAL|AGRO|AGROPECUARIA)",
    re.IGNORECASE,
)


def extrair_orcamento_arquivo(filename: str) -> dict | None:
    if not filename:
        return None
    name = filename.strip()
    for pat in [r"^(\d+)\.pdf$", r"^(CO\d+)\.pdf$"]:
        m = re.match(pat, name, re.IGNORECASE)
        if m:
            return {"numero": m.group(1), "cliente": None, "arquivo": filename}
    for pat in [r"^(\d+)([A-Z][A-Za-z0-9]+?)pdf\.pdf$", r"^(\d+)([A-Z][A-Za-z0-9]+?)\.pdf$"]:
        m = re.match(pat, name, re.IGNORECASE)
        if m:
            raw = m.group(2)
            cliente = re.sub(r"\s+", " ", _PALAVRAS_EMPRESA.sub(lambda x: " " + x.group(), raw)).title().strip()
            return {"numero": m.group(1), "cliente": cliente, "arquivo": filename}
    return None


def _ts_to_dt(ts) -> datetime | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
    except Exception:
        return None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


# =========================================
# FUNÇÕES DE API
# =========================================

@st.cache_data(ttl=600, show_spinner=False)
def carregar_contatos(max_contatos: int = 5000) -> list[dict]:
    contatos = []
    token_cont = None
    while len(contatos) < max_contatos:
        take = min(100, max_contatos - len(contatos))
        hdrs = dict(HEADERS)
        if token_cont:
            hdrs["x-ms-continuation"] = token_cont.replace("PLUS", "+").replace("HASHTAG", "#")
        resp = requests.get(f"{SURI_BASE}/api/contacts", headers=hdrs, params={"take": take}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", {}).get("items", [])
        token_cont = data.get("data", {}).get("continuationToken")
        contatos.extend(items)
        if not items or not token_cont:
            break
    return contatos


@st.cache_data(ttl=600, show_spinner=False)
def carregar_mensagens(contact_id: str, take: int = 200) -> list[dict]:
    try:
        resp = requests.get(
            f"{SURI_BASE}/api/contacts/{contact_id}/messages",
            headers=HEADERS, params={"take": take}, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", []) if isinstance(data.get("data"), list) else []
    except Exception:
        return []


# =========================================
# PARSE DO CONTATO
# =========================================

def parse_contato(c: dict) -> dict:
    ag   = c.get("agent") or {}
    sess = c.get("session") or {}
    data_ref = (
        _parse_dt(c.get("lastMessageActivity"))
        or _parse_dt(c.get("lastActivity"))
        or _parse_dt(c.get("dateCreate"))
    )
    tempo_resp_fila = None
    if ag.get("dateRequest") and ag.get("dateAnswer"):
        req = _parse_dt(ag["dateRequest"])
        ans = _parse_dt(ag["dateAnswer"])
        if req and ans:
            tempo_resp_fila = max(0, (ans - req).total_seconds() / 60)
    dept_id   = ag.get("departmentId") or ""
    status_ag = ag.get("status", 0)
    return {
        "id":                   c.get("id"),
        "nome":                 c.get("name") or "Sem nome",
        "telefone":             c.get("phone"),
        "canal":                c.get("channelId"),
        "data_ref":             data_ref,
        "atendido":             sess.get("answered", False),
        "status_agente":        status_ag,
        "dept_id":              dept_id,
        "tempo_resp_fila_min":  tempo_resp_fila,
        "eh_comercial_contato": dept_id == DEPT_COMERCIAL_ID,
    }


# =========================================
# ANÁLISE COMPLETA DA CONVERSA
# =========================================

def analisar_conversa(cid: str) -> dict:
    msgs = carregar_mensagens(cid)
    if not msgs:
        return {"sem_mensagens": True, "eh_comercial_msgs": False, "intencoes": [], "lacunas_kb": [],
                "frustracoes": [], "codigos_peca": [], "orcamentos_pdf": [], "tem_pdf": False}

    msgs_sorted = sorted(msgs, key=lambda x: x.get("createdAt") or 0)

    msgs_user   = [m for m in msgs_sorted if m.get("type") == "UserMessage"]
    msgs_agente = [m for m in msgs_sorted if m.get("type") == "AgentMessage"]
    msgs_sys    = [m for m in msgs_sorted if m.get("type") == "SystemMessage"]

    # ── Classificação comercial ─────────────────────────────────────────────
    eh_comercial = False
    for m in msgs_sys:
        if "comercial" in (m.get("text") or "").lower():
            eh_comercial = True
    for m in msgs_agente:
        if (m.get("custom") or {}).get("departmentId") == DEPT_COMERCIAL_ID:
            eh_comercial = True

    # ── Intenção do cliente ─────────────────────────────────────────────────
    texto_cliente = " ".join((m.get("text") or "").lower() for m in msgs_user)
    intencoes = [top for top, kws in TOPICOS.items() if any(kw in texto_cliente for kw in kws)]

    # ── Lacunas na KB: bot admitiu que não sabe ─────────────────────────────
    # Para cada mensagem do bot "não sei", identifica qual foi a pergunta anterior
    lacunas_kb = []
    for i, m in enumerate(msgs_sorted):
        if m.get("type") != "AgentMessage":
            continue
        txt_bot = m.get("text") or ""
        if _BOT_NAO_SOUBE.search(txt_bot):
            # Busca a última mensagem do cliente antes dessa
            pergunta = ""
            for j in range(i - 1, -1, -1):
                if msgs_sorted[j].get("type") == "UserMessage":
                    pergunta = (msgs_sorted[j].get("text") or "").strip()
                    break
            lacunas_kb.append({
                "pergunta":     pergunta[:200] if pergunta else "(sem texto)",
                "resposta_bot": txt_bot[:200],
            })

    # ── Frustração do cliente ───────────────────────────────────────────────
    frustracoes = []
    for m in msgs_user:
        txt = m.get("text") or ""
        if _FRUSTRACAO.search(txt):
            frustracoes.append(txt.strip()[:200])

    # ── Transferências para humano ──────────────────────────────────────────
    n_transferencias = sum(
        1 for m in msgs_sys if _TRANSFERENCIA.search(m.get("text") or "")
    )
    motivo_enc = None
    for m in msgs_sys:
        match = re.search(r"Motivo do atendimento:\s*(.+)", m.get("text", ""), re.IGNORECASE)
        if match:
            motivo_enc = match.group(1).strip()
            break

    # ── Resolução sem humano ────────────────────────────────────────────────
    resolvido_bot = n_transferencias == 0 and len(msgs_agente) > 0

    # ── Códigos de peça mencionados pelo cliente ─────────────────────────────
    codigos_peca = list(dict.fromkeys(
        m.upper() for txt in (m.get("text") or "" for m in msgs_user)
        for m in _CODIGO_PECA.findall(txt)
    ))

    # ── Tempo de resposta (via timestamps das mensagens) ─────────────────────
    primeiro_user = next((m for m in msgs_sorted if m.get("type") == "UserMessage"), None)
    primeira_resp = None
    if primeiro_user:
        ts0 = primeiro_user.get("createdAt") or 0
        primeira_resp = next(
            (m for m in msgs_sorted if m.get("type") == "AgentMessage" and (m.get("createdAt") or 0) > ts0),
            None,
        )
    tempo_resp_msgs = None
    if primeiro_user and primeira_resp:
        delta = ((primeira_resp.get("createdAt") or 0) - (primeiro_user.get("createdAt") or 0)) / 1000
        tempo_resp_msgs = max(0, delta / 60)

    ts_list    = [m.get("createdAt") for m in msgs if m.get("createdAt")]
    duracao_min = (max(ts_list) - min(ts_list)) / 1000 / 60 if ts_list else None

    # ── Orçamentos PDF ───────────────────────────────────────────────────────
    orcamentos = []
    for m in msgs_sorted:
        dt_msg = _ts_to_dt(m.get("createdAt"))
        for item in (m.get("content") or []):
            fname = item.get("filename") or item.get("name") or ""
            if fname.lower().endswith(".pdf"):
                info = extrair_orcamento_arquivo(fname)
                if info:
                    info.update({"url": item.get("url", ""), "data_msg": dt_msg, "enviado_por": m.get("type", "")})
                    orcamentos.append(info)
        att = m.get("attachment") or {}
        fname = att.get("name") or att.get("filename") or ""
        if fname.lower().endswith(".pdf"):
            info = extrair_orcamento_arquivo(fname)
            if info:
                info.update({"url": att.get("url", ""), "data_msg": dt_msg, "enviado_por": m.get("type", "")})
                orcamentos.append(info)

    return {
        "sem_mensagens":      False,
        "eh_comercial_msgs":  eh_comercial,
        "n_msgs_cliente":     len(msgs_user),
        "n_msgs_agente":      len(msgs_agente),
        "tempo_resp_msgs_min": tempo_resp_msgs,
        "duracao_conv_min":   duracao_min,
        "motivo_encerramento": motivo_enc,
        "n_transferencias":   n_transferencias,
        "resolvido_bot":      resolvido_bot,
        "intencoes":          intencoes,
        "intencoes_str":      ", ".join(intencoes) if intencoes else "—",
        "lacunas_kb":         lacunas_kb,         # lista de {pergunta, resposta_bot}
        "frustracoes":        frustracoes,         # lista de frases
        "codigos_peca":       codigos_peca,        # lista de strings
        "tem_mencao_orc":     any(kw in texto_cliente for kw in ["orçamento","orcamento","cotação","cotacao","preço","preco","valor","r$"]),
        "orcamentos_pdf":     orcamentos,
        "tem_pdf":            len(orcamentos) > 0,
        "numeros_orc":        ", ".join(dict.fromkeys(o["numero"] for o in orcamentos)) if orcamentos else "",
        "clientes_orc":       ", ".join(dict.fromkeys(o["cliente"] for o in orcamentos if o.get("cliente"))) if orcamentos else "",
    }


# =========================================
# TELA PRINCIPAL
# =========================================

def tela_comercial():
    st.title("🧠 Inteligência Comercial — WhatsApp Suri")
    st.caption("Analise o que os clientes buscam, onde a Suri falha e onde estão as oportunidades de venda.")

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("---")
        st.subheader("⚙️ Filtros")
        data_ini = st.date_input("De",  value=date(2025, 8, 1))
        data_fim = st.date_input("Até", value=date.today())
        apenas_comercial = st.toggle("Apenas canal Comercial", value=True)
        st.markdown("---")
        if st.button("🔄 Recarregar dados"):
            st.cache_data.clear()
            for k in list(st.session_state.keys()):
                if k.startswith("comercial_"):
                    del st.session_state[k]
            st.rerun()

    # ── Contatos ──────────────────────────────────────────────────────────────
    with st.spinner("Carregando contatos..."):
        todos_contatos = carregar_contatos(5000)
    if not todos_contatos:
        st.error("Nenhum contato retornado pela API.")
        return

    df_base = pd.DataFrame([parse_contato(c) for c in todos_contatos])
    df_base = df_base.drop_duplicates(subset="id")
    df_base["data_ref"] = pd.to_datetime(df_base["data_ref"], utc=True)

    corte_ini  = pd.Timestamp(data_ini, tz="UTC")
    corte_fim  = pd.Timestamp(data_fim, tz="UTC") + pd.Timedelta(days=1)
    mask       = (df_base["data_ref"] >= corte_ini) & (df_base["data_ref"] < corte_fim)
    df_periodo = df_base[mask].drop_duplicates(subset="id").copy()

    data_min_ds = df_base["data_ref"].dropna().min().date()
    data_max_ds = df_base["data_ref"].dropna().max().date()
    st.caption(
        f"Base: {data_min_ds:%d/%m/%Y} → {data_max_ds:%d/%m/%Y} · "
        f"{len(todos_contatos):,} contatos · {len(df_periodo):,} no período"
    )

    if df_periodo.empty:
        st.warning("Nenhum contato com atividade no período. Ajuste o filtro.")
        return

    # ── Carrega e analisa mensagens (cache por período) ───────────────────────
    cache_key = f"comercial_{data_ini}_{data_fim}"
    if cache_key not in st.session_state:
        ids   = df_periodo["id"].tolist()
        total = len(ids)
        prog  = st.progress(0, text=f"Analisando {total} conversas…")
        analises: dict[str, dict] = {}
        done = 0
        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = {executor.submit(analisar_conversa, cid): cid for cid in ids}
            for fut in as_completed(futures):
                cid = futures[fut]
                try:
                    analises[cid] = fut.result()
                except Exception:
                    analises[cid] = {"sem_mensagens": True, "eh_comercial_msgs": False,
                                     "intencoes": [], "lacunas_kb": [], "frustracoes": [],
                                     "codigos_peca": [], "orcamentos_pdf": [], "tem_pdf": False}
                done += 1
                if done % 20 == 0 or done == total:
                    prog.progress(done / total, text=f"Mensagens {done}/{total}…")
        prog.empty()
        st.session_state[cache_key] = analises

    analises = st.session_state[cache_key]

    # ── DataFrame completo (sem duplicatas) ──────────────────────────────────
    rows = []
    seen_ids = set()
    for _, row in df_periodo.iterrows():
        cid = row["id"]
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        r = dict(row)
        an = analises.get(cid, {})
        r.update(an)
        r["eh_comercial"]       = r.get("eh_comercial_contato", False) or r.get("eh_comercial_msgs", False)
        r["tempo_resposta_min"] = r.get("tempo_resp_fila_min") or r.get("tempo_resp_msgs_min")
        rows.append(r)

    df_all = pd.DataFrame(rows)
    df     = df_all[df_all["eh_comercial"]].copy() if apenas_comercial else df_all.copy()

    if df.empty:
        st.warning("Nenhuma conversa comercial no período. Desative o filtro.")
        return

    # ── Agrega dados globais usados em múltiplas seções ──────────────────────
    total_conv     = len(df)
    atendidas      = int(df["atendido"].sum())
    em_atend       = int((df["status_agente"] == 2).sum())
    com_pdf        = int(df["tem_pdf"].sum()) if "tem_pdf" in df.columns else 0
    com_mencao     = int(df["tem_mencao_orc"].sum()) if "tem_mencao_orc" in df.columns else 0
    taxa_atend     = atendidas / total_conv * 100 if total_conv else 0
    tempo_med      = df["tempo_resposta_min"].dropna().mean()
    n_lacunas      = int(df["lacunas_kb"].apply(lambda x: len(x) if isinstance(x, list) else 0).sum())
    n_frustracoes  = int(df["frustracoes"].apply(lambda x: len(x) if isinstance(x, list) else 0).sum())
    n_transferidas = int(df["n_transferencias"].fillna(0).gt(0).sum()) if "n_transferencias" in df.columns else 0
    resolvidos_bot = int(df["resolvido_bot"].sum()) if "resolvido_bot" in df.columns else 0

    todos_codigos: list[str] = []
    for _, row in df.iterrows():
        todos_codigos.extend(row.get("codigos_peca") or [])

    todas_lacunas: list[dict] = []
    for _, row in df.iterrows():
        for lac in (row.get("lacunas_kb") or []):
            todas_lacunas.append({"cliente": row["nome"], **lac})

    def detectar_topico(texto: str) -> str:
        t = texto.lower()
        for top, kws in TOPICOS.items():
            if any(kw in t for kw in kws):
                return top
        return "Outros"

    df_lac = pd.DataFrame()
    if todas_lacunas:
        df_lac = pd.DataFrame(todas_lacunas)
        df_lac["topico"] = df_lac["pergunta"].apply(detectar_topico)

    sem_resp = int(df["motivo_encerramento"].str.contains(
        "sem resposta|não respondeu|nao respondeu", case=False, na=False
    ).sum()) if "motivo_encerramento" in df.columns else 0

    # =========================================================================
    # KPIs
    # =========================================================================
    st.markdown("---")
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("💬 Conversas",      f"{total_conv:,}")
    c2.metric("✅ Atendidas",       f"{atendidas:,}",      f"{taxa_atend:.0f}%")
    c3.metric("🤖 Resolveu o bot",  f"{resolvidos_bot:,}", f"{resolvidos_bot/total_conv*100:.0f}%" if total_conv else "")
    c4.metric("🔀 Transferidas",    f"{n_transferidas:,}")
    c5.metric("❓ Lacunas KB",      f"{n_lacunas:,}")
    c6.metric("😤 Frustrações",     f"{n_frustracoes:,}")
    c7.metric("⏱️ 1ª Resposta",    f"{tempo_med:.0f} min" if pd.notna(tempo_med) else "—")

    # =========================================================================
    # ABA PRINCIPAL — navegação entre as grandes visões
    # =========================================================================
    aba_clientes, aba_oportunidades, aba_suri, aba_conversas = st.tabs([
        "🔍 O que os clientes buscam",
        "💡 Oportunidades de Venda",
        "🤖 Melhorar a Suri",
        "📋 Conversas",
    ])

    # =========================================================================
    # ABA 1 — O QUE OS CLIENTES BUSCAM
    # =========================================================================
    with aba_clientes:
        st.markdown("### O que os clientes mais procuram")

        # Intenções: gráfico grande em cima
        if "intencoes" in df.columns:
            df_int = df[df["intencoes"].apply(lambda x: isinstance(x, list) and len(x) > 0)]
            if not df_int.empty:
                cnt_int = df_int["intencoes"].explode().value_counts().reset_index()
                cnt_int.columns = ["topico", "conversas"]
                cnt_int["pct"] = (cnt_int["conversas"] / total_conv * 100).round(1)
                cnt_int["label"] = cnt_int["conversas"].astype(str) + " conv. (" + cnt_int["pct"].astype(str) + "%)"

                fig_int = px.bar(
                    cnt_int, x="conversas", y="topico", orientation="h",
                    color="conversas", color_continuous_scale="Blues",
                    text="label",
                )
                fig_int.update_traces(textposition="outside")
                fig_int.update_layout(
                    height=380, showlegend=False, coloraxis_showscale=False,
                    margin=dict(l=10, r=120, t=10, b=10),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#e0e0e0", xaxis_title="Conversas", yaxis_title="",
                )
                st.plotly_chart(fig_int, use_container_width=True)
            else:
                st.info("Nenhuma intenção detectada.")

        st.markdown("---")
        col_c1, col_c2 = st.columns(2)

        # Códigos de peça
        with col_c1:
            st.markdown("#### 🔩 Peças e códigos mais pedidos")
            if todos_codigos:
                cnt_cod = Counter(todos_codigos).most_common(20)
                df_cod  = pd.DataFrame(cnt_cod, columns=["Código", "Menções"])
                fig_cod = px.bar(
                    df_cod, x="Menções", y="Código", orientation="h",
                    color="Menções", color_continuous_scale="Greens", text="Menções",
                )
                fig_cod.update_traces(textposition="outside")
                fig_cod.update_layout(
                    height=420, showlegend=False, coloraxis_showscale=False,
                    margin=dict(l=10, r=60, t=10, b=10),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#e0e0e0", xaxis_title="Menções", yaxis_title="",
                )
                st.plotly_chart(fig_cod, use_container_width=True)
            else:
                st.info("Nenhum código de peça detectado nas mensagens.")

        # Evolução temporal das intenções
        with col_c2:
            st.markdown("#### 📈 Volume de conversas por dia")
            df_t = df.copy()
            df_t["dia"] = pd.to_datetime(df_t["data_ref"]).dt.date
            if "intencoes" in df_t.columns:
                # Explode intenções por dia para mostrar evolução de cada categoria
                df_exp = df_t[df_t["intencoes"].apply(lambda x: isinstance(x, list) and len(x) > 0)].copy()
                if not df_exp.empty:
                    df_exp = df_exp.explode("intencoes")
                    agr_int = df_exp.groupby(["dia", "intencoes"]).size().reset_index(name="qtd")
                    agr_int.columns = ["dia", "topico", "qtd"]
                    fig_ev = px.line(agr_int, x="dia", y="qtd", color="topico", markers=True)
                    fig_ev.update_layout(
                        height=420, margin=dict(l=10, r=10, t=10, b=10),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        font_color="#e0e0e0", legend_title="Intenção",
                        xaxis_title="", yaxis_title="Conversas",
                    )
                    st.plotly_chart(fig_ev, use_container_width=True)
                else:
                    agr = df_t.groupby("dia").size().reset_index(name="qtd")
                    fig_t2 = px.bar(agr, x="dia", y="qtd")
                    fig_t2.update_layout(height=420, paper_bgcolor="rgba(0,0,0,0)",
                                         plot_bgcolor="rgba(0,0,0,0)", font_color="#e0e0e0")
                    st.plotly_chart(fig_t2, use_container_width=True)

        # Tabela: clientes × intenções × códigos de peça
        st.markdown("---")
        st.markdown("#### 🧾 Detalhe por cliente — o que cada um buscou")
        cols_busca = ["nome", "telefone", "data_ref", "intencoes_str", "codigos_peca", "n_msgs_cliente"]
        df_busca = df[[c for c in cols_busca if c in df.columns]].copy()
        df_busca["data_ref"]      = pd.to_datetime(df_busca["data_ref"]).dt.strftime("%d/%m/%Y")
        df_busca["codigos_peca"]  = df_busca["codigos_peca"].apply(
            lambda x: ", ".join(x) if isinstance(x, list) and x else "—"
        )
        df_busca = df_busca.rename(columns={
            "nome": "Cliente", "telefone": "Telefone", "data_ref": "Data",
            "intencoes_str": "Intenção", "codigos_peca": "Códigos/Peças",
            "n_msgs_cliente": "Msgs",
        })
        df_busca["Intenção"] = df_busca["Intenção"].fillna("—")
        st.dataframe(df_busca.sort_values("Data", ascending=False), use_container_width=True, hide_index=True)

    # =========================================================================
    # ABA 2 — OPORTUNIDADES DE VENDA
    # =========================================================================
    with aba_oportunidades:
        st.markdown("### Onde estão as oportunidades")

        col_o1, col_o2 = st.columns(2)

        # Funil
        with col_o1:
            st.markdown("#### 🔽 Funil de conversas")
            fig_funil = go.Figure(go.Funnel(
                y=["Chegaram ao Comercial", "Bot resolveu sozinho",
                   "Foram atendidas por humano", "Receberam orçamento (PDF)", "Sem resposta do cliente"],
                x=[total_conv, resolvidos_bot, atendidas, com_pdf, sem_resp],
                textposition="inside", textinfo="value+percent initial",
                marker_color=["#4e8df5", "#36b37e", "#00b8d9", "#ff991f", "#ff5630"],
            ))
            fig_funil.update_layout(
                height=340, margin=dict(l=10, r=10, t=10, b=10),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e0e0e0",
            )
            st.plotly_chart(fig_funil, use_container_width=True)

        # Pediram preço mas não receberam orçamento
        with col_o2:
            st.markdown("#### 💰 Pediram preço → não receberam orçamento")
            if "tem_mencao_orc" in df.columns and "tem_pdf" in df.columns:
                df_perd = df[(df["tem_mencao_orc"] == True) & (df["tem_pdf"] == False)]
                pct_perd = len(df_perd) / com_mencao * 100 if com_mencao else 0
                st.metric(
                    "Oportunidades não convertidas",
                    f"{len(df_perd):,}",
                    f"{pct_perd:.0f}% das que pediram preço ficaram sem orçamento",
                    delta_color="inverse",
                )
                if not df_perd.empty:
                    df_pt = df_perd[["nome", "telefone", "data_ref", "intencoes_str", "motivo_encerramento"]].copy()
                    df_pt["data_ref"] = pd.to_datetime(df_pt["data_ref"]).dt.strftime("%d/%m/%Y")
                    df_pt["motivo_encerramento"] = df_pt["motivo_encerramento"].fillna("—")
                    df_pt.columns = ["Cliente", "Telefone", "Data", "Intenção", "Motivo Enc."]
                    st.dataframe(df_pt.sort_values("Data", ascending=False), use_container_width=True, hide_index=True, height=260)

        st.markdown("---")

        # Motivos de encerramento (onde as vendas morrem)
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            st.markdown("#### ❌ Onde as conversas morrem")
            if "motivo_encerramento" in df.columns:
                df_mot = df[df["motivo_encerramento"].notna()]
                if not df_mot.empty:
                    cnt_m = df_mot["motivo_encerramento"].str.strip().value_counts().head(12).reset_index()
                    cnt_m.columns = ["motivo", "qtd"]
                    fig_m = px.bar(cnt_m, x="qtd", y="motivo", orientation="h",
                                   color="qtd", color_continuous_scale="Reds", text="qtd")
                    fig_m.update_traces(textposition="outside")
                    fig_m.update_layout(
                        height=380, showlegend=False, coloraxis_showscale=False,
                        margin=dict(l=10, r=60, t=10, b=10),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        font_color="#e0e0e0", yaxis=dict(autorange="reversed"),
                        xaxis_title="", yaxis_title="",
                    )
                    st.plotly_chart(fig_m, use_container_width=True)
                else:
                    st.info("Sem motivos registrados.")

        with col_m2:
            st.markdown("#### 📊 Conversas com × sem orçamento por dia")
            df_t2 = df.copy()
            df_t2["dia"] = pd.to_datetime(df_t2["data_ref"]).dt.date
            if "tem_pdf" in df_t2.columns:
                agr2 = df_t2.groupby(["dia", "tem_pdf"]).size().reset_index(name="qtd")
                agr2["tipo"] = agr2["tem_pdf"].map({True: "Com orçamento PDF", False: "Sem orçamento"})
                fig_t2 = px.bar(agr2, x="dia", y="qtd", color="tipo", barmode="stack",
                                color_discrete_map={"Com orçamento PDF": "#36b37e", "Sem orçamento": "#4e8df5"})
            else:
                agr2 = df_t2.groupby("dia").size().reset_index(name="qtd")
                fig_t2 = px.bar(agr2, x="dia", y="qtd")
            fig_t2.update_layout(
                height=380, margin=dict(l=10, r=10, t=10, b=10),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e0e0e0", legend_title="", xaxis_title="", yaxis_title="Conversas",
            )
            st.plotly_chart(fig_t2, use_container_width=True)

        # Painel de prioridades
        st.markdown("---")
        st.markdown("#### 🎯 Prioridades — o que fazer primeiro")
        prioridades = []
        if n_lacunas > 0 and not df_lac.empty:
            top_lac = df_lac["topico"].value_counts().index[0]
            prioridades.append({
                "Prioridade": "🔴 Alta",
                "Ação":       f"Adicionar conteúdo à KB sobre '{top_lac}'",
                "Impacto":    f"{n_lacunas} perguntas sem resposta no período",
                "Categoria":  "Base de Conhecimento",
            })
        if n_frustracoes > 0:
            prioridades.append({
                "Prioridade": "🔴 Alta",
                "Ação":       "Melhorar reconhecimento de intenção na Suri",
                "Impacto":    f"{n_frustracoes} mensagens de frustração detectadas",
                "Categoria":  "UX do Bot",
            })
        if n_transferidas > total_conv * 0.4:
            prioridades.append({
                "Prioridade": "🟡 Média",
                "Ação":       "Automatizar respostas dos tópicos mais transferidos",
                "Impacto":    f"{n_transferidas} conversas precisaram de humano ({n_transferidas/total_conv*100:.0f}%)",
                "Categoria":  "Automação",
            })
        if "tem_mencao_orc" in df.columns and "tem_pdf" in df.columns:
            n_perd = len(df[(df["tem_mencao_orc"] == True) & (df["tem_pdf"] == False)])
            if n_perd > 0:
                prioridades.append({
                    "Prioridade": "🟡 Média",
                    "Ação":       "Criar fluxo automático de geração e envio de orçamento",
                    "Impacto":    f"{n_perd} clientes pediram preço e não receberam orçamento",
                    "Categoria":  "Vendas",
                })
        if todos_codigos:
            top_cod = Counter(todos_codigos).most_common(1)[0]
            prioridades.append({
                "Prioridade": "🟢 Baixa",
                "Ação":       f"Verificar se '{top_cod[0]}' está cadastrado com preço na KB",
                "Impacto":    f"Código mais pedido — {top_cod[1]}x mencionado",
                "Categoria":  "Catálogo",
            })
        if prioridades:
            st.dataframe(pd.DataFrame(prioridades), use_container_width=True, hide_index=True)
        else:
            st.success("Nenhuma prioridade crítica identificada no período.")

    # =========================================================================
    # ABA 3 — MELHORAR A SURI
    # =========================================================================
    with aba_suri:
        st.markdown("### Onde a Suri precisa melhorar")

        col_s1, col_s2 = st.columns(2)

        # Lacunas KB
        with col_s1:
            st.markdown("#### ❓ Perguntas que a Suri não soube responder")
            if not df_lac.empty:
                cnt_lac = df_lac["topico"].value_counts().reset_index()
                cnt_lac.columns = ["Categoria", "Ocorrências"]
                fig_lac = px.bar(
                    cnt_lac, x="Ocorrências", y="Categoria", orientation="h",
                    color="Ocorrências", color_continuous_scale="Oranges", text="Ocorrências",
                )
                fig_lac.update_traces(textposition="outside")
                fig_lac.update_layout(
                    height=320, showlegend=False, coloraxis_showscale=False,
                    margin=dict(l=10, r=60, t=10, b=10),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#e0e0e0", xaxis_title="", yaxis_title="",
                )
                st.plotly_chart(fig_lac, use_container_width=True)
                with st.expander(f"Ver {len(df_lac)} perguntas sem resposta"):
                    st.dataframe(
                        df_lac[["topico", "pergunta", "resposta_bot", "cliente"]].rename(columns={
                            "topico": "Categoria", "pergunta": "Pergunta",
                            "resposta_bot": "Resposta da Suri", "cliente": "Cliente",
                        }),
                        use_container_width=True, hide_index=True,
                    )
            else:
                st.success("Nenhuma lacuna detectada no período.")

        # Frustrações
        with col_s2:
            st.markdown("#### 😤 Frustrações dos clientes")
            todas_frust = []
            for _, row in df.iterrows():
                for fr in (row.get("frustracoes") or []):
                    todas_frust.append({"Cliente": row["nome"], "Mensagem": fr})
            if todas_frust:
                def tipo_frust(txt):
                    t = txt.lower()
                    if re.search(r"humano|atendente|pessoa", t):     return "Pediu humano"
                    if re.search(r"não entend|nao entend", t):       return "Bot não entendeu"
                    if re.search(r"já disse|ja disse|já falei", t):  return "Repetição de info"
                    return "Outro"
                df_fr = pd.DataFrame(todas_frust)
                df_fr["Tipo"] = df_fr["Mensagem"].apply(tipo_frust)
                cnt_fr = df_fr["Tipo"].value_counts().reset_index()
                cnt_fr.columns = ["tipo", "qtd"]
                fig_fr = px.pie(cnt_fr, names="tipo", values="qtd", hole=0.45,
                                color_discrete_sequence=px.colors.qualitative.Pastel)
                fig_fr.update_layout(
                    height=320, margin=dict(l=0, r=0, t=20, b=0),
                    paper_bgcolor="rgba(0,0,0,0)", font_color="#e0e0e0",
                )
                st.plotly_chart(fig_fr, use_container_width=True)
                with st.expander(f"Ver {len(df_fr)} frases de frustração"):
                    st.dataframe(df_fr, use_container_width=True, hide_index=True)
            else:
                st.success("Nenhuma frustração detectada.")

    # =========================================================================
    # ABA 4 — CONVERSAS DETALHADAS
    # =========================================================================
    with aba_conversas:
        STATUS_LABEL = {0: "Encerrado", 2: "🔴 Em atendimento"}

        def montar_tabela(frame: pd.DataFrame) -> pd.DataFrame:
            cols = ["nome", "telefone", "data_ref", "status_agente", "intencoes_str",
                    "atendido", "tem_mencao_orc", "tem_pdf", "numeros_orc",
                    "motivo_encerramento", "n_msgs_cliente", "n_msgs_agente",
                    "duracao_conv_min", "tempo_resposta_min", "n_transferencias"]
            fr = frame[[c for c in cols if c in frame.columns]].drop_duplicates(subset=["nome", "telefone", "data_ref"] if "telefone" in frame.columns else ["nome", "data_ref"]).copy()
            rename = {
                "nome": "Cliente", "telefone": "Telefone", "data_ref": "Última msg",
                "status_agente": "Status", "intencoes_str": "Intenção",
                "atendido": "Atendido", "tem_mencao_orc": "Pediu Preço",
                "tem_pdf": "Orçamento PDF", "numeros_orc": "Nº Orçamento(s)",
                "motivo_encerramento": "Motivo Enc.", "n_msgs_cliente": "Msgs Cliente",
                "n_msgs_agente": "Msgs Agente", "duracao_conv_min": "Duração (min)",
                "tempo_resposta_min": "1ª Resp (min)", "n_transferencias": "Transferências",
            }
            fr = fr.rename(columns={k: v for k, v in rename.items() if k in fr.columns})
            fr["Última msg"] = pd.to_datetime(fr["Última msg"]).dt.strftime("%d/%m/%Y %H:%M")
            if "Status"        in fr.columns: fr["Status"]        = fr["Status"].map(STATUS_LABEL).fillna("Encerrado")
            if "Atendido"      in fr.columns: fr["Atendido"]      = fr["Atendido"].map({True: "✅", False: "❌"})
            if "Pediu Preço"   in fr.columns: fr["Pediu Preço"]   = fr["Pediu Preço"].map({True: "✅", False: "—"})
            if "Orçamento PDF" in fr.columns: fr["Orçamento PDF"] = fr["Orçamento PDF"].map({True: "✅", False: "—"})
            for col in ("1ª Resp (min)", "Duração (min)"):
                if col in fr.columns:
                    fr[col] = fr[col].apply(lambda x: f"{x:.0f}" if pd.notna(x) else "—")
            if "Motivo Enc."     in fr.columns: fr["Motivo Enc."]     = fr["Motivo Enc."].fillna("—")
            if "Intenção"        in fr.columns: fr["Intenção"]        = fr["Intenção"].fillna("—")
            if "Nº Orçamento(s)" in fr.columns: fr["Nº Orçamento(s)"] = fr["Nº Orçamento(s)"].fillna("").replace("", "—")
            return fr.sort_values("Última msg", ascending=False)

        tab_todas, tab_atend, tab_pdf, tab_lac_conv = st.tabs([
            "📊 Todas", "🔴 Em atendimento", "📎 Com PDF orçamento", "❓ Com lacunas KB"
        ])

        with tab_todas:
            st.dataframe(montar_tabela(df), use_container_width=True, hide_index=True)

        with tab_atend:
            df_c = df[df["status_agente"] == 2] if "status_agente" in df.columns else pd.DataFrame()
            if df_c.empty:
                st.info("Nenhuma conversa em atendimento no período.")
            else:
                st.dataframe(montar_tabela(df_c), use_container_width=True, hide_index=True)

        with tab_pdf:
            df_c = df[df["tem_pdf"] == True] if "tem_pdf" in df.columns else pd.DataFrame()
            if df_c.empty:
                st.info("Nenhuma conversa com PDF de orçamento no período.")
            else:
                st.dataframe(montar_tabela(df_c), use_container_width=True, hide_index=True)

        with tab_lac_conv:
            df_c = df[df["lacunas_kb"].apply(lambda x: isinstance(x, list) and len(x) > 0)] if "lacunas_kb" in df.columns else pd.DataFrame()
            if df_c.empty:
                st.info("Nenhuma conversa com lacuna de KB no período.")
            else:
                st.dataframe(montar_tabela(df_c), use_container_width=True, hide_index=True)

    st.markdown("---")
    st.caption(
        f"{len(todos_contatos):,} contatos carregados · {len(df_periodo):,} no período · "
        f"{total_conv:,} comerciais · {em_atend} em atendimento · "
        f"Cache 10 min · {datetime.now():%d/%m/%Y %H:%M}"
    )
