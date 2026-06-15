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

TOPICOS = {
    "Peças / Reposição":  ["peça", "peca", "peças", "pecas", "reposição", "reposicao", "componente", "kit"],
    "Preço / Orçamento":  ["preço", "preco", "orçamento", "orcamento", "cotação", "cotacao", "valor", "quanto custa", "r$", "tabela"],
    "Equipamento":        ["equipamento", "máquina", "maquina", "modelo", "marca", "ano", "série", "serie", "chassi"],
    "Urgência":           ["urgente", "urgência", "urgencia", "rápido", "rapido", "hoje", "agora", "preciso logo"],
    "Frete / Entrega":    ["frete", "entrega", "prazo", "transportadora", "envio", "despacho"],
    "Pagamento":          ["pagamento", "boleto", "pix", "cartão", "cartao", "parcelar", "financiar", "à vista"],
}

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
    for pat in [
        r"^(\d+)\.pdf$",
        r"^(CO\d+)\.pdf$",
    ]:
        m = re.match(pat, name, re.IGNORECASE)
        if m:
            return {"numero": m.group(1), "cliente": None, "arquivo": filename}
    for pat in [
        r"^(\d+)([A-Z][A-Za-z0-9]+?)pdf\.pdf$",
        r"^(\d+)([A-Z][A-Za-z0-9]+?)\.pdf$",
    ]:
        m = re.match(pat, name, re.IGNORECASE)
        if m:
            raw = m.group(2)
            cliente = re.sub(
                r"\s+", " ",
                _PALAVRAS_EMPRESA.sub(lambda x: " " + x.group(), raw)
            ).title().strip()
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
def carregar_mensagens(contact_id: str, take: int = 200) -> list[dict]:
    """Carrega até `take` mensagens. Tenta página adicional se retornou take exato."""
    try:
        resp = requests.get(
            f"{SURI_BASE}/api/contacts/{contact_id}/messages",
            headers=HEADERS,
            params={"take": take},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        msgs = data.get("data", []) if isinstance(data.get("data"), list) else []
        return msgs
    except Exception:
        return []


# =========================================
# PARSE DO CONTATO (sem mensagens)
# =========================================

def parse_contato(c: dict) -> dict:
    ag  = c.get("agent") or {}
    sess = c.get("session") or {}

    data_ref = (
        _parse_dt(c.get("lastMessageActivity"))
        or _parse_dt(c.get("lastActivity"))
        or _parse_dt(c.get("dateCreate"))
    )

    # Tempo de resposta inicial da fila (só disponível quando foi atendido)
    tempo_resp_fila = None
    if ag.get("dateRequest") and ag.get("dateAnswer"):
        req = _parse_dt(ag["dateRequest"])
        ans = _parse_dt(ag["dateAnswer"])
        if req and ans:
            tempo_resp_fila = max(0, (ans - req).total_seconds() / 60)

    dept_id  = ag.get("departmentId") or ""
    status_ag = ag.get("status", 0)

    return {
        "id":                  c.get("id"),
        "nome":                c.get("name") or "Sem nome",
        "telefone":            c.get("phone"),
        "canal":               c.get("channelId"),
        "data_ref":            data_ref,
        "atendido":            sess.get("answered", False),
        "status_agente":       status_ag,      # 0=fechado, 2=em atendimento
        "dept_id":             dept_id,
        "tempo_resp_fila_min": tempo_resp_fila,
        "eh_comercial_contato": dept_id == DEPT_COMERCIAL_ID,
    }


# =========================================
# ANÁLISE COMPLETA DA CONVERSA
# =========================================

def analisar_conversa(cid: str) -> dict:
    """Carrega mensagens e extrai TUDO de uma vez. Chamado em paralelo."""
    msgs = carregar_mensagens(cid)
    if not msgs:
        return {"sem_mensagens": True}

    # ── Classificação comercial ─────────────────────────────────────────────
    eh_comercial = False
    for m in msgs:
        txt    = (m.get("text") or "").lower()
        custom = m.get("custom") or {}
        if m.get("type") == "SystemMessage" and "comercial" in txt:
            eh_comercial = True
        if custom.get("departmentId") == DEPT_COMERCIAL_ID:
            eh_comercial = True

    # ── Tempo de resposta via mensagens ─────────────────────────────────────
    # Primeira msg do cliente → primeira resposta do agente depois dela
    msgs_sorted = sorted(msgs, key=lambda x: x.get("createdAt") or 0)
    primeiro_cliente = next(
        (m for m in msgs_sorted if m.get("type") == "UserMessage"), None
    )
    primeira_resposta = None
    if primeiro_cliente:
        ts_cli = primeiro_cliente.get("createdAt") or 0
        primeira_resposta = next(
            (m for m in msgs_sorted
             if m.get("type") == "AgentMessage" and (m.get("createdAt") or 0) > ts_cli),
            None,
        )

    tempo_resp_msgs = None
    if primeiro_cliente and primeira_resposta:
        delta = ((primeira_resposta.get("createdAt") or 0) - (primeiro_cliente.get("createdAt") or 0)) / 1000
        tempo_resp_msgs = max(0, delta / 60)

    # Tempo total da conversa (primeira → última mensagem)
    ts_list = [m.get("createdAt") for m in msgs if m.get("createdAt")]
    duracao_min = None
    if ts_list:
        duracao_min = (max(ts_list) - min(ts_list)) / 1000 / 60

    # ── Contagem de mensagens ───────────────────────────────────────────────
    msgs_cliente = [m for m in msgs if m.get("type") == "UserMessage"]
    msgs_agente  = [m for m in msgs if m.get("type") == "AgentMessage"]
    msgs_sistema = [m for m in msgs if m.get("type") == "SystemMessage"]

    # ── Motivo de encerramento ──────────────────────────────────────────────
    motivo_enc = None
    for m in msgs_sistema:
        match = re.search(r"Motivo do atendimento:\s*(.+)", m.get("text", ""), re.IGNORECASE)
        if match:
            motivo_enc = match.group(1).strip()
            break

    # ── Menção a preço/orçamento no texto ───────────────────────────────────
    KEYWORDS_ORC = [
        "orçamento", "orcamento", "cotação", "cotacao", "preço", "preco",
        "valor", "r$", "quanto custa", "tabela de preços", "proposta", "pedido",
    ]
    tem_mencao_orc = any(
        kw in (m.get("text") or "").lower()
        for m in msgs_cliente
        for kw in KEYWORDS_ORC
    )

    # ── Intenção do cliente (TOPICOS) ────────────────────────────────────────
    texto_cliente = " ".join((m.get("text") or "").lower() for m in msgs_cliente)
    intencoes = [top for top, kws in TOPICOS.items() if any(kw in texto_cliente for kw in kws)]

    # ── Orçamentos via PDF anexado ───────────────────────────────────────────
    orcamentos = []
    for m in msgs:
        dt_msg = _ts_to_dt(m.get("createdAt"))
        # campo content[] (lista de arquivos)
        for item in (m.get("content") or []):
            fname = item.get("filename") or item.get("name") or ""
            if fname.lower().endswith(".pdf"):
                info = extrair_orcamento_arquivo(fname)
                if info:
                    info["url"]      = item.get("url", "")
                    info["data_msg"] = dt_msg
                    info["enviado_por"] = m.get("type", "")
                    orcamentos.append(info)
        # campo attachment{}
        att = m.get("attachment") or {}
        fname = att.get("name") or att.get("filename") or ""
        if fname.lower().endswith(".pdf"):
            info = extrair_orcamento_arquivo(fname)
            if info:
                info["url"]      = att.get("url", "")
                info["data_msg"] = dt_msg
                info["enviado_por"] = m.get("type", "")
                orcamentos.append(info)

    return {
        "eh_comercial_msgs":  eh_comercial,
        "sem_mensagens":      False,
        "n_msgs_cliente":     len(msgs_cliente),
        "n_msgs_agente":      len(msgs_agente),
        "tempo_resp_msgs_min": tempo_resp_msgs,
        "duracao_conv_min":   duracao_min,
        "motivo_encerramento": motivo_enc,
        "tem_mencao_orc":     tem_mencao_orc,
        "intencoes":          intencoes,       # lista de strings
        "intencoes_str":      ", ".join(intencoes) if intencoes else "—",
        "orcamentos_pdf":     orcamentos,      # lista de dicts
        "tem_pdf":            len(orcamentos) > 0,
        "numeros_orc":        ", ".join(dict.fromkeys(o["numero"] for o in orcamentos)) if orcamentos else "",
        "clientes_orc":       ", ".join(dict.fromkeys(
                                  o["cliente"] for o in orcamentos if o.get("cliente")
                              )) if orcamentos else "",
    }


# =========================================
# TELA PRINCIPAL
# =========================================

def tela_comercial():
    st.title("💬 Análise Comercial — WhatsApp")

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("---")
        st.subheader("⚙️ Filtros")
        data_ini = st.date_input("De",  value=date(2025, 8, 1))
        data_fim = st.date_input("Até", value=date.today())
        apenas_comercial = st.toggle("Apenas conversas Comerciais", value=True)
        st.markdown("---")
        if st.button("🔄 Atualizar dados"):
            st.cache_data.clear()
            for k in list(st.session_state.keys()):
                if k.startswith("comercial_"):
                    del st.session_state[k]
            st.rerun()

    # ── Carrega contatos (metadados, sem mensagens) ───────────────────────────
    with st.spinner("Carregando contatos..."):
        todos_contatos = carregar_contatos(5000)

    if not todos_contatos:
        st.error("Nenhum contato retornado pela API.")
        return

    df_base = pd.DataFrame([parse_contato(c) for c in todos_contatos])
    df_base["data_ref"] = pd.to_datetime(df_base["data_ref"], utc=True)

    data_min_ds = df_base["data_ref"].dropna().min().date()
    data_max_ds = df_base["data_ref"].dropna().max().date()

    st.caption(
        f"Dataset: {data_min_ds:%d/%m/%Y} → {data_max_ds:%d/%m/%Y} · "
        f"{len(todos_contatos):,} contatos · filtro: {data_ini:%d/%m/%Y} – {data_fim:%d/%m/%Y}"
    )

    # ── Filtro de período (usa data do contato — instantâneo) ─────────────────
    corte_ini = pd.Timestamp(data_ini, tz="UTC")
    corte_fim = pd.Timestamp(data_fim, tz="UTC") + pd.Timedelta(days=1)
    mask = (df_base["data_ref"] >= corte_ini) & (df_base["data_ref"] < corte_fim)
    df_periodo = df_base[mask].copy()

    if df_periodo.empty:
        st.warning("Nenhum contato com atividade no período. Ajuste o filtro de datas.")
        return

    # ── Análise completa (uma vez por período — cache em session_state) ───────
    cache_key = f"comercial_{data_ini}_{data_fim}"
    if cache_key not in st.session_state:
        ids = df_periodo["id"].tolist()
        total = len(ids)
        prog = st.progress(0, text=f"Analisando {total} conversas…")
        analises: dict[str, dict] = {}
        done = 0

        with ThreadPoolExecutor(max_workers=12) as executor:
            futures = {executor.submit(analisar_conversa, cid): cid for cid in ids}
            for fut in as_completed(futures):
                cid = futures[fut]
                try:
                    analises[cid] = fut.result()
                except Exception:
                    analises[cid] = {"sem_mensagens": True}
                done += 1
                if done % 20 == 0 or done == total:
                    prog.progress(done / total, text=f"Mensagens {done}/{total}…")

        prog.empty()
        st.session_state[cache_key] = analises

    analises: dict[str, dict] = st.session_state[cache_key]

    # ── Monta DataFrame completo ───────────────────────────────────────────────
    rows = []
    for _, row in df_periodo.iterrows():
        r = dict(row)
        an = analises.get(row["id"], {})
        r.update(an)
        r["eh_comercial"] = r.get("eh_comercial_contato", False) or r.get("eh_comercial_msgs", False)
        # Tempo de resposta: prefere fila (já atribuído pelo agente), senão calcula via msgs
        r["tempo_resposta_min"] = r.get("tempo_resp_fila_min") or r.get("tempo_resp_msgs_min")
        rows.append(r)

    df_all = pd.DataFrame(rows)
    df = df_all[df_all["eh_comercial"]].copy() if apenas_comercial else df_all.copy()

    if df.empty:
        st.warning("Nenhuma conversa comercial no período. Desative o filtro para ver todas.")
        return

    # ── KPIs ──────────────────────────────────────────────────────────────────
    st.markdown("---")
    total_conv  = len(df)
    atendidas   = int(df["atendido"].sum())
    em_atend    = int((df["status_agente"] == 2).sum())
    com_pdf     = int(df["tem_pdf"].sum()) if "tem_pdf" in df.columns else 0
    com_mencao  = int(df["tem_mencao_orc"].sum()) if "tem_mencao_orc" in df.columns else 0
    taxa_atend  = atendidas / total_conv * 100 if total_conv else 0
    tempo_med   = df["tempo_resposta_min"].dropna().mean()

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("💬 Conversas",      f"{total_conv:,}")
    c2.metric("✅ Atendidas",       f"{atendidas:,}",  f"{taxa_atend:.0f}%")
    c3.metric("🔴 Em atendimento",  f"{em_atend:,}")
    c4.metric("📎 PDFs orçamento",  f"{com_pdf:,}")
    c5.metric("💰 Menção preço",    f"{com_mencao:,}")
    c6.metric("⏱️ 1ª Resposta",    f"{tempo_med:.0f} min" if pd.notna(tempo_med) else "—")

    st.markdown("---")

    # ── Funil + Motivos de perda ──────────────────────────────────────────────
    col_f, col_m = st.columns(2)

    with col_f:
        st.subheader("🔽 Funil de conversas")
        sem_resp = 0
        if "motivo_encerramento" in df.columns:
            sem_resp = int(df["motivo_encerramento"].str.contains(
                "sem resposta|não respondeu|nao respondeu", case=False, na=False
            ).sum())
        fig_funil = go.Figure(go.Funnel(
            y=["Iniciaram contato", "Canal Comercial", "Foram atendidas",
               "Orçamento (PDF)", "Sem resposta cliente"],
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
                st.info("Nenhum motivo de encerramento registrado no período.")
        else:
            st.info("Nenhum motivo de encerramento registrado no período.")

    st.markdown("---")

    # ── Timeline + Intenção de compra ─────────────────────────────────────────
    col_t, col_i = st.columns([3, 2])

    with col_t:
        st.subheader("📈 Conversas por dia")
        df_t = df.copy()
        df_t["dia"] = pd.to_datetime(df_t["data_ref"]).dt.date
        if "tem_pdf" in df_t.columns:
            agr = df_t.groupby(["dia", "tem_pdf"]).size().reset_index(name="qtd")
            agr["tipo"] = agr["tem_pdf"].map({True: "Com PDF orçamento", False: "Sem PDF"})
            fig_t = px.bar(
                agr, x="dia", y="qtd", color="tipo", barmode="stack",
                color_discrete_map={"Com PDF orçamento": "#36b37e", "Sem PDF": "#4e8df5"},
            )
        else:
            agr = df_t.groupby("dia").size().reset_index(name="qtd")
            fig_t = px.bar(agr, x="dia", y="qtd")
        fig_t.update_layout(
            height=300, margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e0e0e0", legend_title="", xaxis_title="", yaxis_title="Conversas",
        )
        st.plotly_chart(fig_t, use_container_width=True)

    with col_i:
        st.subheader("🔍 Intenção do cliente")
        if "intencoes" in df.columns:
            # Explode a lista de intenções — cada linha vira N linhas se tiver N intenções
            df_int = df[df["intencoes"].apply(lambda x: isinstance(x, list) and len(x) > 0)].copy()
            if not df_int.empty:
                exploded = df_int["intencoes"].explode()
                cnt_int  = exploded.value_counts().reset_index()
                cnt_int.columns = ["topico", "conversas"]
                fig_i = px.bar(
                    cnt_int, x="conversas", y="topico", orientation="h",
                    color="conversas", color_continuous_scale="Blues", text="conversas",
                )
                fig_i.update_traces(textposition="outside")
                fig_i.update_layout(
                    height=300, showlegend=False, coloraxis_showscale=False,
                    margin=dict(l=10, r=10, t=10, b=10),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#e0e0e0", xaxis_title="Conversas", yaxis_title="",
                )
                st.plotly_chart(fig_i, use_container_width=True)
            else:
                st.info("Nenhuma intenção detectada.")
        else:
            st.info("Intenção não disponível — aguarde o carregamento.")

    st.markdown("---")

    # ── Tabelas ───────────────────────────────────────────────────────────────
    st.subheader("📋 Conversas do período")
    STATUS_LABEL = {0: "Encerrado", 2: "🔴 Em atendimento"}

    def montar_tabela(frame: pd.DataFrame) -> pd.DataFrame:
        cols = [
            "nome", "telefone", "data_ref", "status_agente",
            "intencoes_str", "numeros_orc", "clientes_orc",
            "atendido", "tem_mencao_orc", "motivo_encerramento",
            "n_msgs_cliente", "n_msgs_agente", "duracao_conv_min", "tempo_resposta_min",
        ]
        fr = frame[[c for c in cols if c in frame.columns]].copy()
        rename = {
            "nome":               "Cliente",
            "telefone":           "Telefone",
            "data_ref":           "Última msg",
            "status_agente":      "Status",
            "intencoes_str":      "Intenção",
            "numeros_orc":        "Nº Orçamento(s)",
            "clientes_orc":       "Empresa(s) ORC",
            "atendido":           "Atendido",
            "tem_mencao_orc":     "Menção Preço",
            "motivo_encerramento":"Motivo Enc.",
            "n_msgs_cliente":     "Msgs Cliente",
            "n_msgs_agente":      "Msgs Agente",
            "duracao_conv_min":   "Duração (min)",
            "tempo_resposta_min": "1ª Resp (min)",
        }
        fr = fr.rename(columns={k: v for k, v in rename.items() if k in fr.columns})
        fr["Última msg"] = pd.to_datetime(fr["Última msg"]).dt.strftime("%d/%m/%Y %H:%M")
        if "Status"       in fr.columns: fr["Status"]       = fr["Status"].map(STATUS_LABEL).fillna("Encerrado")
        if "Atendido"     in fr.columns: fr["Atendido"]     = fr["Atendido"].map({True: "✅", False: "❌"})
        if "Menção Preço" in fr.columns: fr["Menção Preço"] = fr["Menção Preço"].map({True: "✅", False: "—"})
        for col in ("1ª Resp (min)", "Duração (min)"):
            if col in fr.columns:
                fr[col] = fr[col].apply(lambda x: f"{x:.0f}" if pd.notna(x) else "—")
        if "Motivo Enc."    in fr.columns: fr["Motivo Enc."]    = fr["Motivo Enc."].fillna("—")
        if "Intenção"       in fr.columns: fr["Intenção"]       = fr["Intenção"].fillna("—")
        for col in ("Nº Orçamento(s)", "Empresa(s) ORC"):
            if col in fr.columns:
                fr[col] = fr[col].fillna("").replace("", "—")
        return fr.sort_values("Última msg", ascending=False)

    tab_pdf, tab_atend, tab_todas = st.tabs(
        ["📎 Com orçamento (PDF)", "🔴 Em atendimento", "📊 Todas"]
    )

    with tab_pdf:
        if "tem_pdf" in df.columns:
            df_c = df[df["tem_pdf"] == True]
        else:
            df_c = pd.DataFrame()
        if df_c.empty:
            st.info("Nenhuma conversa com PDF de orçamento no período.")
        else:
            st.dataframe(montar_tabela(df_c), use_container_width=True, hide_index=True)

            # Detalhe dos PDFs com link
            st.markdown("**📎 Arquivos de orçamento encontrados:**")
            pdfs_rows = []
            for _, row in df_c.iterrows():
                for orc in (row.get("orcamentos_pdf") or []):
                    pdfs_rows.append({
                        "Cliente":     row["nome"],
                        "Nº Orc.":     orc.get("numero", ""),
                        "Empresa ORC": orc.get("cliente") or "—",
                        "Arquivo":     orc.get("arquivo", ""),
                        "URL":         orc.get("url", ""),
                        "Data":        orc["data_msg"].strftime("%d/%m/%Y %H:%M") if orc.get("data_msg") else "—",
                        "Enviado por": orc.get("enviado_por", ""),
                    })
            if pdfs_rows:
                df_pdfs = pd.DataFrame(pdfs_rows)
                # Gera coluna com link clicável
                def make_link(row_pdf):
                    if row_pdf["URL"]:
                        return f'<a href="{row_pdf["URL"]}" target="_blank">{row_pdf["Arquivo"]}</a>'
                    return row_pdf["Arquivo"]
                df_pdfs["Arquivo (link)"] = df_pdfs.apply(make_link, axis=1)
                st.write(
                    df_pdfs[["Cliente", "Nº Orc.", "Empresa ORC", "Arquivo (link)", "Data", "Enviado por"]]
                    .to_html(escape=False, index=False),
                    unsafe_allow_html=True,
                )

    with tab_atend:
        df_ea = df[df["status_agente"] == 2] if "status_agente" in df.columns else pd.DataFrame()
        if df_ea.empty:
            st.info("Nenhuma conversa em atendimento no período.")
        else:
            st.dataframe(montar_tabela(df_ea), use_container_width=True, hide_index=True)

    with tab_todas:
        st.dataframe(montar_tabela(df), use_container_width=True, hide_index=True)

    st.markdown("---")
    st.caption(
        f"{len(todos_contatos):,} contatos carregados · {len(df_periodo):,} no período · "
        f"{total_conv:,} comerciais · {em_atend} em atendimento · "
        f"Cache 10 min · {datetime.now():%d/%m/%Y %H:%M}"
    )
