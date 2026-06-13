import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import re
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================================
# CONFIG API
# =========================================

SURI_BASE = "https://cbm-wap-babysuri-cb89467489-dispe.azurewebsites.net"
SURI_TOKEN = "e1c8889c-b971-4f7b-b1ed-39af85da92a3"
DEPT_COMERCIAL_ID = "cb89467499"
HEADERS = {"Authorization": f"Bearer {SURI_TOKEN}"}

# Palavras-chave para detecção de orçamento nas mensagens
KEYWORDS_ORCAMENTO = [
    "orçamento", "orcamento", "cotação", "cotacao", "preço", "preco",
    "valor", "r$", "quanto", "quanto custa", "tabela de preços",
    "tabela de precos", "proposta", "pedido",
]

KEYWORDS_PERDA = [
    "sem resposta", "cliente desistiu", "não respondeu", "nao respondeu",
    "sem interesse", "concorrência", "concorrencia", "preço alto",
    "preco alto", "encerrado", "finalizado",
]

# =========================================
# FUNÇÕES DE API
# =========================================

def _get(path: str, params: dict = None) -> dict:
    resp = requests.get(f"{SURI_BASE}{path}", headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=300, show_spinner=False)
def carregar_contatos(max_contatos: int = 500) -> list[dict]:
    contatos = []
    token_cont = None
    while len(contatos) < max_contatos:
        params = {"take": min(100, max_contatos - len(contatos))}
        if token_cont:
            params["continuationToken"] = token_cont
        data = _get("/api/contacts", params)
        items = data.get("data", {}).get("items", [])
        token_cont = data.get("data", {}).get("continuationToken")
        contatos.extend(items)
        if not items or not token_cont:
            break
    return contatos


@st.cache_data(ttl=300, show_spinner=False)
def carregar_mensagens(contact_id: str, take: int = 50) -> list[dict]:
    try:
        data = _get(f"/api/contacts/{contact_id}/messages", {"take": take})
        return data.get("data", []) if isinstance(data.get("data"), list) else []
    except Exception:
        return []


def extrair_motivo_encerramento(msgs: list[dict]) -> str | None:
    for m in msgs:
        if m.get("type") == "SystemMessage":
            txt = m.get("text", "")
            match = re.search(r"Motivo do atendimento:\s*(.+)", txt, re.IGNORECASE)
            if match:
                return match.group(1).strip()
    return None


def eh_conversa_comercial(msgs: list[dict]) -> bool:
    for m in msgs:
        txt = m.get("text", "").lower()
        custom = m.get("custom", {})
        # SystemMessage indicando departamento Comercial
        if m.get("type") == "SystemMessage" and "comercial" in txt:
            return True
        # AgentMessage com departmentId do Comercial
        if custom.get("departmentId") == DEPT_COMERCIAL_ID:
            return True
    return False


def tem_orcamento(msgs: list[dict]) -> bool:
    for m in msgs:
        if m.get("type") in ("UserMessage", "AgentMessage"):
            txt = m.get("text", "").lower()
            if any(kw in txt for kw in KEYWORDS_ORCAMENTO):
                return True
    return False


def extrair_info_contato(contato: dict) -> dict:
    agent = contato.get("agent", {}) or {}
    session = contato.get("session", {}) or {}

    data_criacao = contato.get("dateCreate") or contato.get("lastActivity")
    try:
        dt = datetime.fromisoformat(data_criacao.replace("Z", "+00:00"))
    except Exception:
        dt = None

    # Tempo de resposta em minutos
    tempo_resp = None
    if agent.get("dateRequest") and agent.get("dateAnswer"):
        try:
            req = datetime.fromisoformat(agent["dateRequest"].replace("Z", "+00:00"))
            ans = datetime.fromisoformat(agent["dateAnswer"].replace("Z", "+00:00"))
            tempo_resp = max(0, (ans - req).total_seconds() / 60)
        except Exception:
            pass

    return {
        "id": contato.get("id"),
        "nome": contato.get("name") or "Sem nome",
        "telefone": contato.get("phone"),
        "canal": contato.get("channelId"),
        "data_criacao": dt,
        "atendido": session.get("answered", False),
        "dept_id": agent.get("departmentId"),
        "agente": agent.get("platformUserId"),
        "status_agente": agent.get("status", 0),
        "tempo_resposta_min": tempo_resp,
        "last_activity": contato.get("lastActivity"),
    }


# =========================================
# TELA PRINCIPAL
# =========================================

def tela_comercial():
    st.title("💬 Análise Comercial — WhatsApp")
    st.caption("Conversas do canal comercial via API Suri em tempo real")

    # ── Sidebar ──────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("---")
        st.subheader("⚙️ Configurações")
        max_contatos = st.slider("Máx. contatos carregados", 50, 500, 200, step=50)
        dias_filtro = st.selectbox("Período", [7, 15, 30, 60, 90, 180], index=2,
                                   format_func=lambda x: f"Últimos {x} dias")
        apenas_comercial = st.toggle("Apenas conversas Comerciais", value=True)
        atualizar = st.button("🔄 Atualizar dados")
        if atualizar:
            st.cache_data.clear()

    # ── Carregamento ─────────────────────────────────────────────────────────
    with st.spinner("Carregando contatos da API Suri..."):
        todos_contatos = carregar_contatos(max_contatos)

    if not todos_contatos:
        st.error("Nenhum contato retornado pela API. Verifique o token.")
        return

    # Extrai infos e descobre o range real dos dados
    todos_infos = [(c, extrair_info_contato(c)) for c in todos_contatos]
    datas_validas = [info["data_criacao"] for _, info in todos_infos if info["data_criacao"]]

    if not datas_validas:
        st.warning("Nenhum contato com data válida.")
        return

    data_max = max(datas_validas)   # data mais recente nos dados
    data_min = min(datas_validas)

    st.caption(
        f"Dados disponíveis: {data_min.strftime('%d/%m/%Y')} → {data_max.strftime('%d/%m/%Y')}"
    )

    # Filtro de período relativo à data mais recente dos dados (não ao "hoje")
    corte = data_max - timedelta(days=dias_filtro)
    contatos_periodo = [
        (c, info) for c, info in todos_infos
        if info["data_criacao"] and info["data_criacao"] >= corte
    ]

    if not contatos_periodo:
        st.warning(
            f"Nenhum contato nos últimos {dias_filtro} dias do dataset "
            f"(referência: {data_max.strftime('%d/%m/%Y')}). "
            "Tente aumentar o período ou o número de contatos carregados."
        )
        return

    # ── Carrega mensagens em paralelo ────────────────────────────────────────
    total = len(contatos_periodo)
    progress = st.progress(0, text=f"Analisando {total} conversas...")

    resultados = []

    def processar(c_info):
        contato, info = c_info
        msgs = carregar_mensagens(info["id"])
        info["eh_comercial"] = eh_conversa_comercial(msgs)
        info["tem_orcamento"] = tem_orcamento(msgs)
        info["motivo_encerramento"] = extrair_motivo_encerramento(msgs)
        info["total_msgs_cliente"] = sum(1 for m in msgs if m.get("type") == "UserMessage")
        info["total_msgs_agente"] = sum(1 for m in msgs if m.get("type") == "AgentMessage")
        info["num_msgs"] = len(msgs)
        return info

    done = 0
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(processar, ci): ci for ci in contatos_periodo}
        for fut in as_completed(futures):
            try:
                resultados.append(fut.result())
            except Exception:
                pass
            done += 1
            progress.progress(done / total, text=f"Analisando conversa {done}/{total}...")

    progress.empty()

    df_all = pd.DataFrame(resultados)
    df = df_all[df_all["eh_comercial"]] if apenas_comercial else df_all

    if df.empty:
        st.warning("Nenhuma conversa comercial encontrada no período.")
        if st.checkbox("Mostrar todas as conversas (sem filtro comercial)"):
            df = df_all
        else:
            return

    # ── KPIs ─────────────────────────────────────────────────────────────────
    st.markdown("---")
    total_conv = len(df)
    atendidas = df["atendido"].sum()
    com_orcamento = df["tem_orcamento"].sum()
    taxa_atend = (atendidas / total_conv * 100) if total_conv else 0
    taxa_orc = (com_orcamento / total_conv * 100) if total_conv else 0
    tempo_med = df["tempo_resposta_min"].dropna().mean()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("💬 Conversas", f"{total_conv:,}")
    col2.metric("✅ Atendidas", f"{int(atendidas):,}", f"{taxa_atend:.0f}%")
    col3.metric("📋 Com Orçamento", f"{int(com_orcamento):,}", f"{taxa_orc:.0f}%")
    col4.metric("⏱️ Resp. Média", f"{tempo_med:.0f} min" if pd.notna(tempo_med) else "—")
    col5.metric("👤 Período", f"{dias_filtro} dias")

    st.markdown("---")

    # ── Funil de Vendas ───────────────────────────────────────────────────────
    col_funil, col_perda = st.columns([1, 1])

    with col_funil:
        st.subheader("🔽 Funil de Vendas")

        sem_resposta = df[
            df["motivo_encerramento"].str.contains("sem resposta|não respondeu|nao respondeu",
                                                    case=False, na=False)
        ]
        convertidas = df[df["tem_orcamento"] & df["atendido"]]
        perdidas = df[
            df["motivo_encerramento"].notna() &
            ~df["tem_orcamento"]
        ]

        etapas = ["Iniciaram conversa", "Pediram Comercial", "Foram atendidas",
                  "Receberam orçamento", "Fechadas sem resposta"]
        valores = [
            len(df_all),
            len(df),
            int(atendidas),
            int(com_orcamento),
            len(sem_resposta),
        ]

        fig_funil = go.Figure(go.Funnel(
            y=etapas,
            x=valores,
            textposition="inside",
            textinfo="value+percent initial",
            marker_color=["#4e8df5", "#36b37e", "#00b8d9", "#ff991f", "#ff5630"],
        ))
        fig_funil.update_layout(
            height=350,
            margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e0e0e0",
        )
        st.plotly_chart(fig_funil, use_container_width=True)

    # ── Motivos de Perda ─────────────────────────────────────────────────────
    with col_perda:
        st.subheader("❌ Onde estou perdendo")

        df_motivos = df[df["motivo_encerramento"].notna()].copy()
        if not df_motivos.empty:
            contagem = (
                df_motivos["motivo_encerramento"]
                .str.strip()
                .value_counts()
                .reset_index()
            )
            contagem.columns = ["motivo", "qtd"]
            contagem = contagem.head(10)

            fig_motivos = px.bar(
                contagem,
                x="qtd",
                y="motivo",
                orientation="h",
                color="qtd",
                color_continuous_scale="Reds",
                text="qtd",
            )
            fig_motivos.update_traces(textposition="outside")
            fig_motivos.update_layout(
                height=350,
                showlegend=False,
                coloraxis_showscale=False,
                margin=dict(l=10, r=10, t=10, b=10),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e0e0e0",
                yaxis=dict(autorange="reversed"),
                xaxis_title="",
                yaxis_title="",
            )
            st.plotly_chart(fig_motivos, use_container_width=True)
        else:
            st.info("Nenhum motivo de encerramento registrado no período.")

    st.markdown("---")

    # ── Timeline de conversas ─────────────────────────────────────────────────
    col_time, col_canal = st.columns([2, 1])

    with col_time:
        st.subheader("📈 Conversas por dia")
        df_time = df.copy()
        df_time["dia"] = pd.to_datetime(df_time["data_criacao"]).dt.date
        agrupado = (
            df_time.groupby(["dia", "tem_orcamento"])
            .size()
            .reset_index(name="qtd")
        )
        agrupado["tipo"] = agrupado["tem_orcamento"].map(
            {True: "Com orçamento", False: "Sem orçamento"}
        )
        fig_time = px.bar(
            agrupado,
            x="dia",
            y="qtd",
            color="tipo",
            barmode="stack",
            color_discrete_map={"Com orçamento": "#36b37e", "Sem orçamento": "#4e8df5"},
        )
        fig_time.update_layout(
            height=280,
            margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e0e0e0",
            legend_title="",
            xaxis_title="",
            yaxis_title="Conversas",
        )
        st.plotly_chart(fig_time, use_container_width=True)

    with col_canal:
        st.subheader("📡 Por canal")
        canal_count = df["canal"].value_counts().reset_index()
        canal_count.columns = ["canal", "qtd"]
        canal_count["canal_label"] = canal_count["canal"].str.replace("wp", "WA-")
        fig_canal = px.pie(
            canal_count,
            names="canal_label",
            values="qtd",
            hole=0.4,
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig_canal.update_layout(
            height=280,
            margin=dict(l=0, r=0, t=20, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#e0e0e0",
            showlegend=True,
            legend=dict(orientation="v"),
        )
        st.plotly_chart(fig_canal, use_container_width=True)

    st.markdown("---")

    # ── Intenção de compra — palavras mais frequentes ─────────────────────────
    st.subheader("🔍 Intenção de compra — tópicos detectados")

    TOPICOS = {
        "Peças / Reposição": ["peça", "peca", "peças", "pecas", "reposição", "reposicao", "componente"],
        "Preço / Orçamento": ["preço", "preco", "orçamento", "orcamento", "cotação", "cotacao", "valor"],
        "Equipamento": ["equipamento", "máquina", "maquina", "modelo", "marca", "ano"],
        "Urgência": ["urgente", "urgência", "urgencia", "rápido", "rapido", "hoje", "agora"],
        "Dúvida / Info": ["dúvida", "duvida", "informação", "informacao", "como funciona", "pergunta"],
        "Frete / Entrega": ["frete", "entrega", "prazo", "transportadora", "envio"],
        "Pagamento": ["pagamento", "boleto", "pix", "cartão", "cartao", "parcelar", "parcelado"],
    }

    contagens_topicos = {}
    for contato_id, grp in df.set_index("id").iterrows():
        msgs = carregar_mensagens(contato_id)
        texto_cliente = " ".join(
            m.get("text", "").lower()
            for m in msgs
            if m.get("type") == "UserMessage"
        )
        for topico, kws in TOPICOS.items():
            if any(kw in texto_cliente for kw in kws):
                contagens_topicos[topico] = contagens_topicos.get(topico, 0) + 1

    if contagens_topicos:
        df_topicos = pd.DataFrame(
            list(contagens_topicos.items()), columns=["topico", "mencoes"]
        ).sort_values("mencoes", ascending=True)

        fig_top = px.bar(
            df_topicos,
            x="mencoes",
            y="topico",
            orientation="h",
            color="mencoes",
            color_continuous_scale="Blues",
            text="mencoes",
        )
        fig_top.update_traces(textposition="outside")
        fig_top.update_layout(
            height=300,
            showlegend=False,
            coloraxis_showscale=False,
            margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e0e0e0",
            xaxis_title="Nº de conversas com menção",
            yaxis_title="",
        )
        st.plotly_chart(fig_top, use_container_width=True)

    st.markdown("---")

    # ── Tabela de conversas ───────────────────────────────────────────────────
    st.subheader("📋 Conversas recentes")

    tab_orc, tab_todas = st.tabs(["📋 Com orçamento", "📊 Todas"])

    colunas_exibir = {
        "nome": "Cliente",
        "telefone": "Telefone",
        "data_criacao": "Data",
        "atendido": "Atendido",
        "tem_orcamento": "Orçamento",
        "motivo_encerramento": "Motivo Encerramento",
        "total_msgs_cliente": "Msgs Cliente",
        "total_msgs_agente": "Msgs Agente",
        "tempo_resposta_min": "Resp (min)",
    }

    def formatar_df(frame: pd.DataFrame) -> pd.DataFrame:
        fr = frame[list(colunas_exibir.keys())].copy()
        fr = fr.rename(columns=colunas_exibir)
        fr["Data"] = pd.to_datetime(fr["Data"]).dt.strftime("%d/%m/%Y %H:%M")
        fr["Atendido"] = fr["Atendido"].map({True: "✅", False: "❌"})
        fr["Orçamento"] = fr["Orçamento"].map({True: "✅", False: "—"})
        fr["Resp (min)"] = fr["Resp (min)"].apply(
            lambda x: f"{x:.0f}" if pd.notna(x) else "—"
        )
        fr["Motivo Encerramento"] = fr["Motivo Encerramento"].fillna("—")
        return fr.sort_values("Data", ascending=False)

    with tab_orc:
        df_orc = df[df["tem_orcamento"]]
        if df_orc.empty:
            st.info("Nenhuma conversa com menção a orçamento no período.")
        else:
            st.dataframe(formatar_df(df_orc), use_container_width=True, hide_index=True)

    with tab_todas:
        st.dataframe(formatar_df(df), use_container_width=True, hide_index=True)

    # ── Rodapé ────────────────────────────────────────────────────────────────
    st.markdown("---")
    st.caption(
        f"Dados carregados de {len(todos_contatos)} contatos · "
        f"Canal Comercial: {len(df)} conversas · "
        f"Cache: 5 min · Última atualização: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
