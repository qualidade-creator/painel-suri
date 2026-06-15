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

KEYWORDS_ORCAMENTO = [
    "orçamento", "orcamento", "cotação", "cotacao", "preço", "preco",
    "valor", "r$", "quanto custa", "tabela de preços", "proposta", "pedido",
]

# =========================================
# EXTRAÇÃO DE ORÇAMENTO DO NOME DO ARQUIVO
# =========================================

# Palavras comuns em razões sociais — usadas para segmentar o nome da empresa
_PALAVRAS_EMPRESA = re.compile(
    r"(LTDA|EIRELI|EIRELE|EPP|ME|SA|S\.A|S/A|CONS|CONSTR|CONSTRU|MINERACAO"
    r"|CONSTRUCAO|SERVICOS|COMERCIO|INDUSTRIA|TRANSPORTES|AGROPECUARIA"
    r"|ENGENHARIA|SOLUCOES|TECNOLOGIA|EQUIPAMENTOS|MANUTENCAO|PECAS|DIESEL"
    r"|CONSORCIO|GROUP|GRUPO|BRASIL|NACIONAL|INTERNACIONAL)",
    re.IGNORECASE,
)


def extrair_orcamento_arquivo(filename: str) -> dict | None:
    """
    Extrai número do orçamento e nome do cliente a partir do nome do arquivo.

    Padrões suportados:
      452905.pdf                              -> num=452905
      CO01744733.PDF                          -> num=CO01744733
      393031G3CONSTRUCAOPESADALTDApdf.pdf    -> num=393031, cliente=G3 Construcao Pesada Ltda
      447114NEWZPECASLTDAMEpdf.pdf           -> num=447114, cliente=New Pecas Ltda Me
    """
    if not filename:
        return None
    name = filename.strip()

    # Padrão 1: apenas dígitos (ex: 452905.pdf)
    m = re.match(r"^(\d+)\.pdf$", name, re.IGNORECASE)
    if m:
        return {"numero": m.group(1), "cliente": None, "arquivo": filename}

    # Padrão 2: CO + dígitos (ex: CO01744733.PDF)
    m = re.match(r"^(CO\d+)\.pdf$", name, re.IGNORECASE)
    if m:
        return {"numero": m.group(1), "cliente": None, "arquivo": filename}

    # Padrão 3: dígitos + nome_empresa + pdf.pdf
    m = re.match(r"^(\d+)([A-ZÁÉÍÓÚÀÂÊÔÃÕÇZ][A-Za-záéíóúàâêôãõçZz0-9]+?)pdf\.pdf$",
                 name, re.IGNORECASE)
    if m:
        numero = m.group(1)
        cliente_raw = m.group(2)
        # Formata: insere espaço antes de palavras-chave conhecidas
        cliente = re.sub(
            r"([A-Z])([A-Z][a-z])",
            lambda x: x.group(1) + " " + x.group(2),
            cliente_raw,
        )
        # Insere espaço antes de palavras de razão social em uppercase
        cliente = _PALAVRAS_EMPRESA.sub(lambda x: " " + x.group(), cliente).strip()
        # Remove espaços duplos e capitaliza
        cliente = re.sub(r"\s+", " ", cliente).title().strip()
        return {"numero": numero, "cliente": cliente, "arquivo": filename}

    # Padrão 4: dígitos + nome sem sufixo pdf (ex: 447114NEWZPECASLTDAME.pdf)
    m = re.match(r"^(\d+)([A-ZÁÉÍÓÚ][A-Za-záéíóúàâêôãõçZ0-9]+?)\.pdf$",
                 name, re.IGNORECASE)
    if m:
        numero = m.group(1)
        cliente_raw = m.group(2)
        cliente = _PALAVRAS_EMPRESA.sub(lambda x: " " + x.group(), cliente_raw).strip()
        cliente = re.sub(r"\s+", " ", cliente).title().strip()
        return {"numero": numero, "cliente": cliente, "arquivo": filename}

    return None


def extrair_orcamentos_msgs(msgs: list[dict]) -> list[dict]:
    """Retorna lista de orçamentos encontrados nos anexos das mensagens."""
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
                    info["url"] = item.get("url", "")
                    info["data_msg"] = data_msg
                    info["tipo_msg"] = m.get("type", "")
                    orcamentos.append(info)

        # Também verifica campo attachment direto
        att = m.get("attachment") or {}
        fname = att.get("name", "") or att.get("filename", "")
        if fname and fname.lower().endswith(".pdf"):
            info = extrair_orcamento_arquivo(fname)
            if info and info not in orcamentos:
                info["url"] = att.get("url", "")
                info["data_msg"] = data_msg
                info["tipo_msg"] = m.get("type", "")
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
# HELPERS DE ANÁLISE
# =========================================

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
        custom = m.get("custom", {}) or {}
        if m.get("type") == "SystemMessage" and "comercial" in txt:
            return True
        if custom.get("departmentId") == DEPT_COMERCIAL_ID:
            return True
    return False


def tem_orcamento_texto(msgs: list[dict]) -> bool:
    for m in msgs:
        if m.get("type") in ("UserMessage", "AgentMessage"):
            txt = m.get("text", "").lower()
            if any(kw in txt for kw in KEYWORDS_ORCAMENTO):
                return True
    return False


def data_ultima_mensagem(msgs: list[dict]) -> datetime | None:
    """Retorna a data da mensagem mais recente."""
    datas = []
    for m in msgs:
        ts = m.get("createdAt")
        if ts:
            try:
                datas.append(datetime.fromtimestamp(ts / 1000, tz=timezone.utc))
            except Exception:
                pass
    return max(datas) if datas else None


def extrair_info_contato(contato: dict) -> dict:
    agent = contato.get("agent", {}) or {}
    session = contato.get("session", {}) or {}

    data_criacao = contato.get("dateCreate") or contato.get("lastActivity")
    try:
        dt = datetime.fromisoformat(data_criacao.replace("Z", "+00:00"))
    except Exception:
        dt = None

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
        "tempo_resposta_min": tempo_resp,
    }


# =========================================
# TELA PRINCIPAL
# =========================================

def tela_comercial():
    st.title("💬 Análise Comercial — WhatsApp")
    st.caption("Conversas do canal comercial via API Suri em tempo real")

    # ── Carregamento dos contatos ─────────────────────────────────────────────
    with st.spinner("Carregando contatos da API Suri..."):
        todos_contatos = carregar_contatos(5000)

    if not todos_contatos:
        st.error("Nenhum contato retornado pela API. Verifique o token.")
        return

    todos_infos = [(c, extrair_info_contato(c)) for c in todos_contatos]

    # ── Análise de mensagens (com progresso) ─────────────────────────────────
    if "dados_comercial" not in st.session_state:
        total = len(todos_infos)
        progress = st.progress(0, text=f"Carregando mensagens de {total} contatos...")

        resultados = []

        def processar(c_info):
            contato, info = c_info
            msgs = carregar_mensagens(info["id"])
            info["eh_comercial"] = eh_conversa_comercial(msgs)
            info["tem_orcamento"] = tem_orcamento_texto(msgs)
            info["motivo_encerramento"] = extrair_motivo_encerramento(msgs)
            info["orcamentos_pdf"] = extrair_orcamentos_msgs(msgs)
            info["tem_pdf"] = len(info["orcamentos_pdf"]) > 0
            info["total_msgs_cliente"] = sum(1 for m in msgs if m.get("type") == "UserMessage")
            info["total_msgs_agente"] = sum(1 for m in msgs if m.get("type") == "AgentMessage")
            # Data da última mensagem (pode ser mês diferente do cadastro)
            info["data_ultima_msg"] = data_ultima_mensagem(msgs)
            return info

        done = 0
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(processar, ci): ci for ci in todos_infos}
            for fut in as_completed(futures):
                try:
                    resultados.append(fut.result())
                except Exception:
                    pass
                done += 1
                if done % 50 == 0:
                    progress.progress(done / total, text=f"Mensagens {done}/{total}...")

        progress.empty()
        st.session_state["dados_comercial"] = resultados

    resultados = st.session_state["dados_comercial"]
    df_all = pd.DataFrame(resultados)

    # Data de referência = data da última mensagem (cobre todos os meses)
    df_all["data_ref"] = df_all["data_ultima_msg"].fillna(df_all["data_criacao"])
    df_all["data_ref"] = pd.to_datetime(df_all["data_ref"], utc=True)

    datas_validas = df_all["data_ref"].dropna()
    if datas_validas.empty:
        st.warning("Nenhum contato com data válida.")
        return

    data_min = datas_validas.min().date()
    data_max = datas_validas.max().date()

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("---")
        st.subheader("⚙️ Filtros")

        data_ini = st.date_input("De", value=data_min,
                                  min_value=data_min, max_value=data_max)
        data_fim = st.date_input("Até", value=data_max,
                                  min_value=data_min, max_value=data_max)
        apenas_comercial = st.toggle("Apenas conversas Comerciais", value=True)

        st.markdown("---")
        if st.button("🔄 Atualizar dados"):
            st.cache_data.clear()
            if "dados_comercial" in st.session_state:
                del st.session_state["dados_comercial"]
            st.rerun()

    st.caption(
        f"Período disponível: {data_min.strftime('%d/%m/%Y')} → {data_max.strftime('%d/%m/%Y')} "
        f"· {len(todos_contatos):,} contatos carregados"
    )

    # ── Filtro por período ────────────────────────────────────────────────────
    corte_ini = pd.Timestamp(data_ini, tz="UTC")
    corte_fim = pd.Timestamp(data_fim, tz="UTC") + pd.Timedelta(days=1)

    mask_periodo = (df_all["data_ref"] >= corte_ini) & (df_all["data_ref"] < corte_fim)
    df_periodo = df_all[mask_periodo]

    df = df_periodo[df_periodo["eh_comercial"]] if apenas_comercial else df_periodo

    if df.empty:
        st.warning("Nenhuma conversa encontrada no período selecionado.")
        if not apenas_comercial:
            return
        if st.checkbox("Mostrar todas as conversas (sem filtro comercial)"):
            df = df_periodo
        else:
            return

    # ── KPIs ──────────────────────────────────────────────────────────────────
    st.markdown("---")
    total_conv = len(df)
    atendidas = int(df["atendido"].sum())
    com_orc_texto = int(df["tem_orcamento"].sum())
    com_pdf = int(df["tem_pdf"].sum())
    taxa_atend = (atendidas / total_conv * 100) if total_conv else 0
    tempo_med = df["tempo_resposta_min"].dropna().mean()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("💬 Conversas", f"{total_conv:,}")
    c2.metric("✅ Atendidas", f"{atendidas:,}", f"{taxa_atend:.0f}%")
    c3.metric("📋 Orçamentos (PDF)", f"{com_pdf:,}")
    c4.metric("💰 Menção Preço", f"{com_orc_texto:,}")
    c5.metric("⏱️ Resp. Média", f"{tempo_med:.0f} min" if pd.notna(tempo_med) else "—")

    st.markdown("---")

    # ── Funil + Motivos ───────────────────────────────────────────────────────
    col_funil, col_perda = st.columns(2)

    with col_funil:
        st.subheader("🔽 Funil de Vendas")
        sem_resposta = df[
            df["motivo_encerramento"].str.contains(
                "sem resposta|não respondeu|nao respondeu", case=False, na=False
            )
        ]
        etapas = ["Iniciaram conversa", "Pediram Comercial",
                  "Foram atendidas", "PDF de orçamento", "Sem resposta"]
        valores = [len(df_periodo), len(df), atendidas, com_pdf, len(sem_resposta)]

        fig_funil = go.Figure(go.Funnel(
            y=etapas, x=valores,
            textposition="inside", textinfo="value+percent initial",
            marker_color=["#4e8df5", "#36b37e", "#00b8d9", "#ff991f", "#ff5630"],
        ))
        fig_funil.update_layout(
            height=350, margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e0e0e0",
        )
        st.plotly_chart(fig_funil, use_container_width=True)

    with col_perda:
        st.subheader("❌ Onde estou perdendo")
        df_mot = df[df["motivo_encerramento"].notna()].copy()
        if not df_mot.empty:
            contagem = (
                df_mot["motivo_encerramento"].str.strip()
                .value_counts().reset_index()
            )
            contagem.columns = ["motivo", "qtd"]
            contagem = contagem.head(10)
            fig_mot = px.bar(
                contagem, x="qtd", y="motivo", orientation="h",
                color="qtd", color_continuous_scale="Reds", text="qtd",
            )
            fig_mot.update_traces(textposition="outside")
            fig_mot.update_layout(
                height=350, showlegend=False, coloraxis_showscale=False,
                margin=dict(l=10, r=10, t=10, b=10),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e0e0e0",
                yaxis=dict(autorange="reversed"), xaxis_title="", yaxis_title="",
            )
            st.plotly_chart(fig_mot, use_container_width=True)
        else:
            st.info("Nenhum motivo de encerramento registrado no período.")

    st.markdown("---")

    # ── Timeline + Canal ─────────────────────────────────────────────────────
    col_time, col_canal = st.columns([2, 1])

    with col_time:
        st.subheader("📈 Conversas por dia")
        df_time = df.copy()
        df_time["dia"] = df_time["data_ref"].dt.date
        agr = (
            df_time.groupby(["dia", "tem_pdf"])
            .size().reset_index(name="qtd")
        )
        agr["tipo"] = agr["tem_pdf"].map({True: "Com PDF orçamento", False: "Sem PDF"})
        fig_time = px.bar(
            agr, x="dia", y="qtd", color="tipo", barmode="stack",
            color_discrete_map={"Com PDF orçamento": "#36b37e", "Sem PDF": "#4e8df5"},
        )
        fig_time.update_layout(
            height=280, margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e0e0e0", legend_title="", xaxis_title="", yaxis_title="Conversas",
        )
        st.plotly_chart(fig_time, use_container_width=True)

    with col_canal:
        st.subheader("📡 Por canal")
        canal_count = df["canal"].value_counts().reset_index()
        canal_count.columns = ["canal", "qtd"]
        canal_count["label"] = canal_count["canal"].str.replace("wp", "WA-")
        fig_canal = px.pie(
            canal_count, names="label", values="qtd", hole=0.4,
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig_canal.update_layout(
            height=280, margin=dict(l=0, r=0, t=20, b=0),
            paper_bgcolor="rgba(0,0,0,0)", font_color="#e0e0e0",
        )
        st.plotly_chart(fig_canal, use_container_width=True)

    st.markdown("---")

    # ── Intenção de compra ────────────────────────────────────────────────────
    st.subheader("🔍 Intenção de compra — tópicos detectados")
    TOPICOS = {
        "Peças / Reposição": ["peça", "peca", "peças", "pecas", "reposição", "componente"],
        "Preço / Orçamento": ["preço", "preco", "orçamento", "orcamento", "cotação", "valor"],
        "Equipamento": ["equipamento", "máquina", "maquina", "modelo", "marca", "ano"],
        "Urgência": ["urgente", "urgência", "rápido", "rapido", "hoje", "agora"],
        "Frete / Entrega": ["frete", "entrega", "prazo", "transportadora", "envio"],
        "Pagamento": ["pagamento", "boleto", "pix", "cartão", "parcelar"],
    }
    contagens_top = {}
    for row in df.itertuples():
        msgs = carregar_mensagens(row.id)
        texto = " ".join(
            m.get("text", "").lower() for m in msgs if m.get("type") == "UserMessage"
        )
        for top, kws in TOPICOS.items():
            if any(kw in texto for kw in kws):
                contagens_top[top] = contagens_top.get(top, 0) + 1

    if contagens_top:
        df_top = pd.DataFrame(
            list(contagens_top.items()), columns=["topico", "mencoes"]
        ).sort_values("mencoes", ascending=True)
        fig_top = px.bar(
            df_top, x="mencoes", y="topico", orientation="h",
            color="mencoes", color_continuous_scale="Blues", text="mencoes",
        )
        fig_top.update_traces(textposition="outside")
        fig_top.update_layout(
            height=300, showlegend=False, coloraxis_showscale=False,
            margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#e0e0e0", xaxis_title="Nº de conversas", yaxis_title="",
        )
        st.plotly_chart(fig_top, use_container_width=True)

    st.markdown("---")

    # ── Orçamentos por PDF ────────────────────────────────────────────────────
    st.subheader("📎 Orçamentos enviados (PDF)")

    linhas_orc = []
    for row in df.itertuples():
        for orc in (row.orcamentos_pdf or []):
            linhas_orc.append({
                "Número ORC": orc.get("numero", "—"),
                "Cliente (arquivo)": orc.get("cliente") or "—",
                "Contato": row.nome,
                "Telefone": row.telefone or "—",
                "Data": orc["data_msg"].strftime("%d/%m/%Y %H:%M") if orc.get("data_msg") else "—",
                "Enviado por": "Cliente" if orc["tipo_msg"] == "UserMessage" else "Agente",
                "Arquivo": orc.get("arquivo", ""),
            })

    if linhas_orc:
        df_orc = pd.DataFrame(linhas_orc).sort_values("Data", ascending=False)
        st.dataframe(df_orc, use_container_width=True, hide_index=True)
        st.caption(f"{len(df_orc)} orçamentos em PDF encontrados no período")
    else:
        st.info("Nenhum orçamento em PDF encontrado no período selecionado.")

    st.markdown("---")

    # ── Tabela geral ─────────────────────────────────────────────────────────
    st.subheader("📋 Conversas do período")
    tab_com, tab_todas = st.tabs(["📎 Com PDF", "📊 Todas"])

    def fmt(frame: pd.DataFrame) -> pd.DataFrame:
        fr = frame[[
            "nome", "telefone", "data_ref", "atendido",
            "tem_pdf", "tem_orcamento", "motivo_encerramento",
            "total_msgs_cliente", "total_msgs_agente", "tempo_resposta_min",
        ]].copy()
        fr.columns = [
            "Cliente", "Telefone", "Última msg", "Atendido",
            "PDF Orc.", "Menção Preço", "Motivo Enc.",
            "Msgs Cliente", "Msgs Agente", "Resp (min)",
        ]
        fr["Última msg"] = pd.to_datetime(fr["Última msg"]).dt.strftime("%d/%m/%Y %H:%M")
        fr["Atendido"] = fr["Atendido"].map({True: "✅", False: "❌"})
        fr["PDF Orc."] = fr["PDF Orc."].map({True: "✅", False: "—"})
        fr["Menção Preço"] = fr["Menção Preço"].map({True: "✅", False: "—"})
        fr["Resp (min)"] = fr["Resp (min)"].apply(lambda x: f"{x:.0f}" if pd.notna(x) else "—")
        fr["Motivo Enc."] = fr["Motivo Enc."].fillna("—")
        return fr.sort_values("Última msg", ascending=False)

    with tab_com:
        df_c = df[df["tem_pdf"]]
        if df_c.empty:
            st.info("Nenhuma conversa com PDF no período.")
        else:
            st.dataframe(fmt(df_c), use_container_width=True, hide_index=True)

    with tab_todas:
        st.dataframe(fmt(df), use_container_width=True, hide_index=True)

    # ── Rodapé ────────────────────────────────────────────────────────────────
    st.markdown("---")
    st.caption(
        f"{len(todos_contatos):,} contatos carregados · "
        f"{len(df):,} conversas comerciais no período · "
        f"Cache: 10 min · {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
