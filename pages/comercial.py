import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import re
from datetime import datetime, date, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================================
# CONFIG API
# =========================================

SURI_BASE = "https://cbm-wap-babysuri-cb89467489-dispe.azurewebsites.net"
SURI_TOKEN = "e1c8889c-b971-4f7b-b1ed-39af85da92a3"
DEPT_COMERCIAL_ID = "cb89467499"
HEADERS = {"Authorization": f"Bearer {SURI_TOKEN}"}

KEYWORDS_ORCAMENTO = [
    "orçamento", "orcamento", "cotação", "cotacao", "preço", "preco",
    "valor", "r$", "quanto custa", "tabela de preços", "proposta", "pedido",
]

# =========================================
# EXTRAÇÃO DE ORÇAMENTO DO NOME DO ARQUIVO
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
    m = re.match(r"^(\d+)\.pdf$", name, re.IGNORECASE)
    if m:
        return {"numero": m.group(1), "cliente": None, "arquivo": filename}
    m = re.match(r"^(CO\d+)\.pdf$", name, re.IGNORECASE)
    if m:
        return {"numero": m.group(1), "cliente": None, "arquivo": filename}
    m = re.match(r"^(\d+)([A-Z][A-Za-z0-9]+?)pdf\.pdf$", name, re.IGNORECASE)
    if m:
        raw = m.group(2)
        cliente = re.sub(r"\s+", " ", _PALAVRAS_EMPRESA.sub(lambda x: " " + x.group(), raw)).title().strip()
        return {"numero": m.group(1), "cliente": cliente, "arquivo": filename}
    m = re.match(r"^(\d+)([A-Z][A-Za-z0-9]+?)\.pdf$", name, re.IGNORECASE)
    if m:
        raw = m.group(2)
        cliente = re.sub(r"\s+", " ", _PALAVRAS_EMPRESA.sub(lambda x: " " + x.group(), raw)).title().strip()
        return {"numero": m.group(1), "cliente": cliente, "arquivo": filename}
    return None


def extrair_orcamentos_msgs(msgs: list[dict]) -> list[dict]:
    orcamentos = []
    for m in msgs:
        data_msg = None
        ts = m.get("createdAt")
        if ts:
            try:
                data_msg = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            except Exception:
                pass
        for item in (m.get("content") or []):
            fname = item.get("filename", "")
            if fname and fname.lower().endswith(".pdf"):
                info = extrair_orcamento_arquivo(fname)
                if info:
                    info.update({"url": item.get("url", ""), "data_msg": data_msg, "tipo_msg": m.get("type", "")})
                    orcamentos.append(info)
        att = m.get("attachment") or {}
        fname = att.get("name", "") or att.get("filename", "")
        if fname and fname.lower().endswith(".pdf"):
            info = extrair_orcamento_arquivo(fname)
            if info:
                info.update({"url": att.get("url", ""), "data_msg": data_msg, "tipo_msg": m.get("type", "")})
                orcamentos.append(info)
    return orcamentos


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
        resp = requests.get(
            f"{SURI_BASE}/api/contacts",
            headers=hdrs,
            params={"take": take},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", {}).get("items", [])
        token_cont = data.get("data", {}).get("continuationToken")
        contatos.extend(items)
        if not items or not token_cont:
            break
    return contatos


@st.cache_data(ttl=600, show_spinner=False)
def carregar_mensagens(contact_id: str, take: int = 100) -> list[dict]:
    try:
        resp = requests.get(
            f"{SURI_BASE}/api/contacts/{contact_id}/messages",
            headers=HEADERS,
            params={"take": take},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", []) if isinstance(data.get("data"), list) else []
    except Exception:
        return []


# =========================================
# PARSE DO CONTATO (sem precisar de mensagens)
# =========================================

def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def parse_contato(c: dict) -> dict:
    """Extrai tudo que o objeto de contato já traz, sem carregar mensagens."""
    ag = c.get("agent") or {}
    sess = c.get("session") or {}

    # Data de referência = última atividade de mensagem (mais precisa que dateCreate)
    data_ref = (
        _parse_dt(c.get("lastMessageActivity"))
        or _parse_dt(c.get("lastActivity"))
        or _parse_dt(c.get("dateCreate"))
    )

    tempo_resp = None
    if ag.get("dateRequest") and ag.get("dateAnswer"):
        try:
            req = _parse_dt(ag["dateRequest"])
            ans = _parse_dt(ag["dateAnswer"])
            if req and ans:
                tempo_resp = max(0, (ans - req).total_seconds() / 60)
        except Exception:
            pass

    # Comercial detectável direto no contato se houver departmentId ou status=2
    dept_id = ag.get("departmentId") or ""
    status_ag = ag.get("status", 0)
    eh_comercial_contato = (dept_id == DEPT_COMERCIAL_ID) or (
        status_ag == 2 and dept_id == DEPT_COMERCIAL_ID
    )

    return {
        "id": c.get("id"),
        "nome": c.get("name") or "Sem nome",
        "telefone": c.get("phone"),
        "canal": c.get("channelId"),
        "data_ref": data_ref,
        "atendido": sess.get("answered", False),
        "status_agente": status_ag,           # 0=fechado, 2=em atendimento
        "dept_id": dept_id,
        "tempo_resposta_min": tempo_resp,
        "eh_comercial_contato": eh_comercial_contato,
    }


# =========================================
# HELPERS DE MENSAGENS
# =========================================

def extrair_motivo_encerramento(msgs: list[dict]) -> str | None:
    for m in msgs:
        if m.get("type") == "SystemMessage":
            match = re.search(r"Motivo do atendimento:\s*(.+)", m.get("text", ""), re.IGNORECASE)
            if match:
                return match.group(1).strip()
    return None


def eh_conversa_comercial_msgs(msgs: list[dict]) -> bool:
    for m in msgs:
        txt = m.get("text", "").lower()
        custom = m.get("custom") or {}
        if m.get("type") == "SystemMessage" and "comercial" in txt:
            return True
        if custom.get("departmentId") == DEPT_COMERCIAL_ID:
            return True
    return False


def tem_orcamento_texto(msgs: list[dict]) -> bool:
    for m in msgs:
        if m.get("type") in ("UserMessage", "AgentMessage"):
            if any(kw in m.get("text", "").lower() for kw in KEYWORDS_ORCAMENTO):
                return True
    return False


# =========================================
# TELA PRINCIPAL
# =========================================

def tela_comercial():
    st.title("💬 Análise Comercial — WhatsApp")

    # ── Sidebar: filtros dinâmicos (não dependem dos dados) ───────────────────
    with st.sidebar:
        st.markdown("---")
        st.subheader("⚙️ Filtros")
        data_ini = st.date_input("De", value=date(2025, 8, 1))
        data_fim = st.date_input("Até", value=date.today())
        apenas_comercial = st.toggle("Apenas conversas Comerciais", value=True)
        st.markdown("---")
        if st.button("🔄 Atualizar dados"):
            st.cache_data.clear()
            for k in list(st.session_state.keys()):
                if k.startswith("comercial"):
                    del st.session_state[k]
            st.rerun()

    # ── Carrega contatos (rápido — só metadados) ──────────────────────────────
    with st.spinner("Carregando contatos..."):
        todos_contatos = carregar_contatos(5000)

    if not todos_contatos:
        st.error("Nenhum contato retornado pela API.")
        return

    # Parse sem mensagens — usa lastMessageActivity do próprio contato
    df_base = pd.DataFrame([parse_contato(c) for c in todos_contatos])
    df_base["data_ref"] = pd.to_datetime(df_base["data_ref"], utc=True)

    data_min_ds = df_base["data_ref"].dropna().min().date()
    data_max_ds = df_base["data_ref"].dropna().max().date()

    st.caption(
        f"Dataset: {data_min_ds.strftime('%d/%m/%Y')} → {data_max_ds.strftime('%d/%m/%Y')} "
        f"· {len(todos_contatos):,} contatos · filtro: {data_ini.strftime('%d/%m/%Y')} – {data_fim.strftime('%d/%m/%Y')}"
    )

    # ── Filtro de período (instantâneo — sem recarregar mensagens) ────────────
    corte_ini = pd.Timestamp(data_ini, tz="UTC")
    corte_fim = pd.Timestamp(data_fim, tz="UTC") + pd.Timedelta(days=1)
    mask = (df_base["data_ref"] >= corte_ini) & (df_base["data_ref"] < corte_fim)
    df_periodo = df_base[mask].copy()

    if df_periodo.empty:
        st.warning("Nenhum contato com atividade no período selecionado.")
        return

    # ── Carrega mensagens APENAS para os contatos do período filtrado ─────────
    cache_key = f"comercial_{data_ini}_{data_fim}"
    if cache_key not in st.session_state:
        ids_periodo = df_periodo["id"].tolist()
        total = len(ids_periodo)
        prog = st.progress(0, text=f"Analisando {total} conversas do período...")

        analises = {}

        def analisar(cid):
            msgs = carregar_mensagens(cid)
            orcs = extrair_orcamentos_msgs(msgs)
            return {
                "eh_comercial_msgs": eh_conversa_comercial_msgs(msgs),
                "tem_orcamento": tem_orcamento_texto(msgs),
                "orcamentos_pdf": orcs,
                "tem_pdf": len(orcs) > 0,
                "numeros_orc": ", ".join(o["numero"] for o in orcs) if orcs else "",
                "clientes_orc": ", ".join(dict.fromkeys(
                    o["cliente"] for o in orcs if o.get("cliente")
                )),
                "motivo_encerramento": extrair_motivo_encerramento(msgs),
                "total_msgs_cliente": sum(1 for m in msgs if m.get("type") == "UserMessage"),
                "total_msgs_agente": sum(1 for m in msgs if m.get("type") == "AgentMessage"),
            }

        done = 0
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(analisar, cid): cid for cid in ids_periodo}
            for fut in as_completed(futures):
                cid = futures[fut]
                try:
                    analises[cid] = fut.result()
                except Exception:
                    analises[cid] = {}
                done += 1
                if done % 30 == 0:
                    prog.progress(done / total, text=f"Mensagens {done}/{total}...")

        prog.empty()
        st.session_state[cache_key] = analises

    analises = st.session_state[cache_key]

    # Junta análise de mensagens com os dados do contato
    rows = []
    for _, row in df_periodo.iterrows():
        r = dict(row)
        r.update(analises.get(row["id"], {}))
        # Comercial = detectado no contato OU nas mensagens
        r["eh_comercial"] = r.get("eh_comercial_contato", False) or r.get("eh_comercial_msgs", False)
        rows.append(r)

    df_all = pd.DataFrame(rows)
    df = df_all[df_all["eh_comercial"]] if apenas_comercial else df_all

    if df.empty:
        st.warning("Nenhuma conversa comercial no período. Desative o filtro para ver todas.")
        return

    # ── KPIs ──────────────────────────────────────────────────────────────────
    st.markdown("---")
    total_conv = len(df)
    atendidas = int(df["atendido"].sum())
    em_atend = int((df["status_agente"] == 2).sum())
    com_pdf = int(df.get("tem_pdf", pd.Series(False)).sum())
    com_mencao = int(df.get("tem_orcamento", pd.Series(False)).sum())
    taxa_atend = (atendidas / total_conv * 100) if total_conv else 0
    tempo_med = df["tempo_resposta_min"].dropna().mean()

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("💬 Conversas", f"{total_conv:,}")
    c2.metric("✅ Atendidas", f"{atendidas:,}", f"{taxa_atend:.0f}%")
    c3.metric("🔴 Em atendimento", f"{em_atend:,}")
    c4.metric("📎 PDFs (orc.)", f"{com_pdf:,}")
    c5.metric("💰 Menção preço", f"{com_mencao:,}")
    c6.metric("⏱️ Resp. Média", f"{tempo_med:.0f} min" if pd.notna(tempo_med) else "—")

    st.markdown("---")

    # ── Funil + Motivos ───────────────────────────────────────────────────────
    col_f, col_m = st.columns(2)

    with col_f:
        st.subheader("🔽 Funil de Vendas")
        sem_resp = int(df["motivo_encerramento"].str.contains(
            "sem resposta|não respondeu|nao respondeu", case=False, na=False
        ).sum()) if "motivo_encerramento" in df.columns else 0

        fig_funil = go.Figure(go.Funnel(
            y=["Iniciaram conversa", "Pediram Comercial",
               "Foram atendidas", "PDF de orçamento", "Sem resposta"],
            x=[len(df_all), total_conv, atendidas, com_pdf, sem_resp],
            textposition="inside", textinfo="value+percent initial",
            marker_color=["#4e8df5", "#36b37e", "#00b8d9", "#ff991f", "#ff5630"],
        ))
        fig_funil.update_layout(
            height=350, margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e0e0e0",
        )
        st.plotly_chart(fig_funil, use_container_width=True)

    with col_m:
        st.subheader("❌ Onde estou perdendo")
        if "motivo_encerramento" in df.columns:
            df_mot = df[df["motivo_encerramento"].notna()].copy()
        else:
            df_mot = pd.DataFrame()

        if not df_mot.empty:
            cnt = df_mot["motivo_encerramento"].str.strip().value_counts().reset_index()
            cnt.columns = ["motivo", "qtd"]
            fig_mot = px.bar(
                cnt.head(10), x="qtd", y="motivo", orientation="h",
                color="qtd", color_continuous_scale="Reds", text="qtd",
            )
            fig_mot.update_traces(textposition="outside")
            fig_mot.update_layout(
                height=350, showlegend=False, coloraxis_showscale=False,
                margin=dict(l=10, r=10, t=10, b=10),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e0e0e0", yaxis=dict(autorange="reversed"),
                xaxis_title="", yaxis_title="",
            )
            st.plotly_chart(fig_mot, use_container_width=True)
        else:
            st.info("Nenhum motivo de encerramento no período.")

    st.markdown("---")

    # ── Timeline + Canal ─────────────────────────────────────────────────────
    col_t, col_c = st.columns([2, 1])

    with col_t:
        st.subheader("📈 Conversas por dia")
        df_t = df.copy()
        df_t["dia"] = pd.to_datetime(df_t["data_ref"]).dt.date
        tem_pdf_col = "tem_pdf" if "tem_pdf" in df_t.columns else None
        if tem_pdf_col:
            agr = df_t.groupby(["dia", tem_pdf_col]).size().reset_index(name="qtd")
            agr["tipo"] = agr[tem_pdf_col].map({True: "Com PDF orçamento", False: "Sem PDF"})
            fig_t = px.bar(
                agr, x="dia", y="qtd", color="tipo", barmode="stack",
                color_discrete_map={"Com PDF orçamento": "#36b37e", "Sem PDF": "#4e8df5"},
            )
        else:
            agr = df_t.groupby("dia").size().reset_index(name="qtd")
            fig_t = px.bar(agr, x="dia", y="qtd")
        fig_t.update_layout(
            height=280, margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e0e0e0", legend_title="", xaxis_title="", yaxis_title="Conversas",
        )
        st.plotly_chart(fig_t, use_container_width=True)

    with col_c:
        st.subheader("📡 Por canal")
        cc = df["canal"].value_counts().reset_index()
        cc.columns = ["canal", "qtd"]
        cc["label"] = cc["canal"].str.replace("wp", "WA-")
        fig_c = px.pie(cc, names="label", values="qtd", hole=0.4,
                       color_discrete_sequence=px.colors.qualitative.Set2)
        fig_c.update_layout(
            height=280, margin=dict(l=0, r=0, t=20, b=0),
            paper_bgcolor="rgba(0,0,0,0)", font_color="#e0e0e0",
        )
        st.plotly_chart(fig_c, use_container_width=True)

    st.markdown("---")

    # ── Intenção de compra ────────────────────────────────────────────────────
    st.subheader("🔍 Intenção de compra")
    TOPICOS = {
        "Peças / Reposição": ["peça", "peca", "peças", "pecas", "reposição", "componente"],
        "Preço / Orçamento": ["preço", "preco", "orçamento", "orcamento", "cotação", "valor"],
        "Equipamento":       ["equipamento", "máquina", "maquina", "modelo", "marca", "ano"],
        "Urgência":          ["urgente", "urgência", "rápido", "rapido", "hoje", "agora"],
        "Frete / Entrega":   ["frete", "entrega", "prazo", "transportadora", "envio"],
        "Pagamento":         ["pagamento", "boleto", "pix", "cartão", "parcelar"],
    }
    top_cnt = {}
    for row in df.itertuples():
        msgs = carregar_mensagens(row.id)
        texto = " ".join(m.get("text", "").lower() for m in msgs if m.get("type") == "UserMessage")
        for top, kws in TOPICOS.items():
            if any(kw in texto for kw in kws):
                top_cnt[top] = top_cnt.get(top, 0) + 1

    if top_cnt:
        df_top = pd.DataFrame(list(top_cnt.items()), columns=["topico", "mencoes"]).sort_values("mencoes")
        fig_top = px.bar(df_top, x="mencoes", y="topico", orientation="h",
                         color="mencoes", color_continuous_scale="Blues", text="mencoes")
        fig_top.update_traces(textposition="outside")
        fig_top.update_layout(
            height=300, showlegend=False, coloraxis_showscale=False,
            margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e0e0e0", xaxis_title="Nº de conversas", yaxis_title="",
        )
        st.plotly_chart(fig_top, use_container_width=True)

    st.markdown("---")

    # ── Tabela de conversas com orçamentos condensados ────────────────────────
    st.subheader("📋 Conversas do período")
    tab_pdf, tab_todas = st.tabs(["📎 Com orçamento (PDF)", "📊 Todas"])

    STATUS_LABEL = {0: "Encerrado", 2: "🔴 Em atendimento"}

    def montar_tabela(frame: pd.DataFrame) -> pd.DataFrame:
        cols = ["nome", "telefone", "data_ref", "status_agente",
                "numeros_orc", "clientes_orc",
                "atendido", "tem_orcamento", "motivo_encerramento",
                "total_msgs_cliente", "total_msgs_agente", "tempo_resposta_min"]
        fr = frame[[c for c in cols if c in frame.columns]].copy()
        rename = {
            "nome": "Cliente", "telefone": "Telefone", "data_ref": "Última msg",
            "status_agente": "Status", "numeros_orc": "Nº Orçamento(s)",
            "clientes_orc": "Empresa(s) ORC", "atendido": "Atendido",
            "tem_orcamento": "Menção Preço", "motivo_encerramento": "Motivo Enc.",
            "total_msgs_cliente": "Msgs Cliente", "total_msgs_agente": "Msgs Agente",
            "tempo_resposta_min": "Resp (min)",
        }
        fr = fr.rename(columns={k: v for k, v in rename.items() if k in fr.columns})
        fr["Última msg"] = pd.to_datetime(fr["Última msg"]).dt.strftime("%d/%m/%Y %H:%M")
        if "Status" in fr.columns:
            fr["Status"] = fr["Status"].map(STATUS_LABEL).fillna("Encerrado")
        if "Atendido" in fr.columns:
            fr["Atendido"] = fr["Atendido"].map({True: "✅", False: "❌"})
        if "Menção Preço" in fr.columns:
            fr["Menção Preço"] = fr["Menção Preço"].map({True: "✅", False: "—"})
        if "Resp (min)" in fr.columns:
            fr["Resp (min)"] = fr["Resp (min)"].apply(lambda x: f"{x:.0f}" if pd.notna(x) else "—")
        if "Motivo Enc." in fr.columns:
            fr["Motivo Enc."] = fr["Motivo Enc."].fillna("—")
        for col in ["Nº Orçamento(s)", "Empresa(s) ORC"]:
            if col in fr.columns:
                fr[col] = fr[col].replace("", "—")
        return fr.sort_values("Última msg", ascending=False)

    with tab_pdf:
        df_c = df[df.get("tem_pdf", pd.Series(False))] if "tem_pdf" in df.columns else pd.DataFrame()
        if df_c.empty:
            st.info("Nenhuma conversa com PDF de orçamento no período.")
        else:
            st.dataframe(montar_tabela(df_c), use_container_width=True, hide_index=True)

    with tab_todas:
        st.dataframe(montar_tabela(df), use_container_width=True, hide_index=True)

    st.markdown("---")
    st.caption(
        f"{len(todos_contatos):,} contatos · {len(df_periodo):,} no período · "
        f"{total_conv:,} comerciais · {em_atend} em atendimento · "
        f"Cache 10 min · {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
