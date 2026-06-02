import streamlit as st
import pandas as pd
import plotly.express as px

def tela_vendas():

    st.title("📊SURI")

    # =========================================
    # GOOGLE SHEETS CSV
    # =========================================

    url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSbVXSXDTNbFlksOfjuHYvK9_ZFJIS_2pGHo9_ZNjjHSAteH738T9WJJiw5hDZKzTpHYuGcQj_c5r7w/pub?gid=440676239&single=true&output=csv"
    # =========================================
    # DASHBOARD VENDAS
    # =========================================


    # =========================================
    # LEITURA DOS DADOS
    # =========================================

    @st.cache_data(ttl=30)
    def carregar_dados():

        df = pd.read_csv(
            
            url,
            sep=None,
            engine="python",
            on_bad_lines="skip"
        )

        # padronizar colunas
        df.columns = (
            df.columns
            .str.strip()
            .str.lower()
            .str.replace(" ", "_")
        )

        return df


    df = carregar_dados()

    # =========================================
    # COLUNAS
    # =========================================

    COLUNA_DATA = "data"
    COLUNA_DATA_PEDIDO = "dt_pedido"
    COLUNA_VALOR = "valor"
    COLUNA_VENDEDOR = "vendedor"
    COLUNA_PEDIDO = "pedido"
    COLUNA_ORCAMENTO = "orçamento"

    # =========================================
    # CONVERSÃO VALOR
    # =========================================

    df[COLUNA_VALOR] = (
        df[COLUNA_VALOR]
        .astype(str)
        .str.replace("R$", "", regex=False)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip()
    )

    df[COLUNA_VALOR] = pd.to_numeric(
        df[COLUNA_VALOR],
        errors="coerce"
    )

    # =========================================
    # CONVERSÃO DATA
    # =========================================

    df[COLUNA_DATA] = pd.to_datetime(
        df[COLUNA_DATA],
        dayfirst=True,
        errors="coerce"
    )

    # =========================================
    # MÊS/ANO
    # =========================================

    # coluna real para ordenação
    df["mes_ano_data"] = (
        df[COLUNA_DATA]
        .dt.to_period("M")
        .dt.to_timestamp()
    )

    # coluna visual
    df["mes_ano"] = (
        df["mes_ano_data"]
        .dt.strftime("%m/%Y")
    )

    # =========================================
    # FILTROS
    # =========================================

    st.sidebar.header("Filtros")

    # vendedor
    vendedores = st.sidebar.multiselect(
        "Vendedor",
        options=df[COLUNA_VENDEDOR].dropna().unique(),
        default=df[COLUNA_VENDEDOR].dropna().unique()
    )

    # período
    data_min = df[COLUNA_DATA].min()
    data_max = df[COLUNA_DATA].max()

    periodo = st.sidebar.date_input(
        "Período",
        value=(data_min, data_max)
    )

    # =========================================
    # FILTRO GERAL
    # =========================================

    df_filtrado = df.copy()

    # vendedor
    df_filtrado = df_filtrado[
        df_filtrado[COLUNA_VENDEDOR].isin(vendedores)
    ]

    # período
    if len(periodo) == 2:

        data_inicio = pd.to_datetime(periodo[0])
        data_fim = pd.to_datetime(periodo[1])

        df_filtrado = df_filtrado[
            (df_filtrado[COLUNA_DATA] >= data_inicio) &
            (df_filtrado[COLUNA_DATA] <= data_fim)
        ]

    # =========================================
    # PEDIDOS VÁLIDOS
    # =========================================

    df_pedidos = df_filtrado[
        df_filtrado[COLUNA_PEDIDO].notna()
    ]

    df_pedidos = df_pedidos[
        df_pedidos[COLUNA_PEDIDO].astype(str).str.strip() != ""
    ]

    # =========================================
    # TOTAL DE ORÇAMENTOS
    # =========================================

    # toda linha da base é um orçamento
    df_orcamentos = df_filtrado.copy()

    quantidade_orcamentos = len(df_orcamentos)
    print(quantidade_orcamentos)

    # =========================================
    # KPIs
    # =========================================

    faturamento = df_pedidos[COLUNA_VALOR].sum()

    quantidade_vendas = df_pedidos[COLUNA_PEDIDO].nunique()

    ticket_medio = 0

    if quantidade_vendas > 0:
        ticket_medio = faturamento / quantidade_vendas

    # =========================================
    # KPIs CONVERSAO
    # =========================================

    
    conversao = 0

    if quantidade_orcamentos > 0:

        conversao = (
            quantidade_vendas /
            quantidade_orcamentos
        ) * 100


# =========================================
# CRESCIMENTO MENSAL
# =========================================

    fat_crescimento = (
        df_pedidos
        .groupby(["mes_ano_data", "mes_ano"])[COLUNA_VALOR]
        .sum()
        .reset_index()
        .sort_values("mes_ano_data")
    )

    crescimento_percentual = 0

    if len(fat_crescimento) >= 2:

        valor_atual = fat_crescimento.iloc[-1][COLUNA_VALOR]
        valor_anterior = fat_crescimento.iloc[-2][COLUNA_VALOR]

        if valor_anterior > 0:

            crescimento_percentual = (
                (
                    valor_atual - valor_anterior
                ) / valor_anterior
            ) * 100


# =========================================
# DELTAS KPIs
# =========================================

# PEDIDOS POR MÊS
    pedidos_mes = (
        df_pedidos
        .groupby(["mes_ano_data", "mes_ano"])[COLUNA_PEDIDO]
        .nunique()
        .reset_index(name="pedidos")
        .sort_values("mes_ano_data")
    )

    crescimento_pedidos = 0

    if len(pedidos_mes) >= 2:

        atual = pedidos_mes.iloc[-1]["pedidos"]
        anterior = pedidos_mes.iloc[-2]["pedidos"]

        if anterior > 0:

            crescimento_pedidos = (
                (atual - anterior) / anterior
            ) * 100


    # =========================================
    # TICKET MÉDIO
    # =========================================

    ticket_delta = (
        df_pedidos
        .groupby(["mes_ano_data", "mes_ano"])
        .agg(
            faturamento=(COLUNA_VALOR, "sum"),
            pedidos=(COLUNA_PEDIDO, "nunique")
        )
        .reset_index()
        .sort_values("mes_ano_data")
    )

    ticket_delta["ticket"] = (
        ticket_delta["faturamento"] /
        ticket_delta["pedidos"]
    )

    crescimento_ticket = 0

    if len(ticket_delta) >= 2:

        atual = ticket_delta.iloc[-1]["ticket"]
        anterior = ticket_delta.iloc[-2]["ticket"]

        if anterior > 0:

            crescimento_ticket = (
                (atual - anterior) / anterior
            ) * 100


    # =========================================
    # ORÇAMENTOS
    # =========================================

    orcamentos_mes = (
        df_orcamentos
        .groupby(["mes_ano_data", "mes_ano"])
        .size()
        .reset_index(name="orcamentos")
        .sort_values("mes_ano_data")
    )

    crescimento_orcamentos = 0

    if len(orcamentos_mes) >= 2:

        atual = orcamentos_mes.iloc[-1]["orcamentos"]
        anterior = orcamentos_mes.iloc[-2]["orcamentos"]

        if anterior > 0:

            crescimento_orcamentos = (
                (atual - anterior) / anterior
            ) * 100


    # =========================================
    # CONVERSÃO
    # =========================================

    conv_mes = pd.merge(
        pedidos_mes,
        orcamentos_mes,
        on=["mes_ano_data", "mes_ano"],
        how="outer"
    ).fillna(0)

    conv_mes["conversao"] = (
        conv_mes["pedidos"] /
        conv_mes["orcamentos"]
    ) * 100

    crescimento_conversao = 0

    if len(conv_mes) >= 2:

        atual = conv_mes.iloc[-1]["conversao"]
        anterior = conv_mes.iloc[-2]["conversao"]

        if anterior > 0:

            crescimento_conversao = (
                (atual - anterior) / anterior
            ) * 100

    # =========================================
    # KPIs VISUAIS
    # =========================================

    col1, col2, col3, col4, col5 = st.columns(5)

    col1.metric(
        "💰 Faturamento",
        f"R$ {faturamento:,.2f}",
        delta=f"{crescimento_percentual:.1f}%"
    )

    col2.metric(
    "🛒 Pedidos",
    quantidade_vendas,
    delta=f"{crescimento_pedidos:.1f}%"
    )

    col3.metric(
    "📦 Ticket Médio",
    f"R$ {ticket_medio:,.2f}",
    delta=f"{crescimento_ticket:.1f}%"
    )

    col4.metric(
    "📈 Conversão",
    f"{conversao:.1f}%",
    delta=f"{crescimento_conversao:.1f}%"
    )

    col5.metric(
    "📋 Orçamentos",
    quantidade_orcamentos,
    delta=f"{crescimento_orcamentos:.1f}%"
    )

    st.divider()

# =========================================
# RANKING VENDEDORES
# =========================================

    ranking = (
        df_pedidos
        .groupby(COLUNA_VENDEDOR)
        .agg(
            faturamento=(COLUNA_VALOR, "sum"),
            pedidos=(COLUNA_PEDIDO, "nunique")
        )
        .reset_index()
    )

    ranking["ticket_medio"] = (
        ranking["faturamento"] /
        ranking["pedidos"]
    )

    ranking = ranking.sort_values(
        "faturamento",
        ascending=True
    )

    fig_rank = px.bar(
        ranking,
        x="faturamento",
        y=COLUNA_VENDEDOR,
        orientation="h",
        title="🏆 Ranking de Vendedores",
        text_auto=True
    )

    st.plotly_chart(
        fig_rank,
        use_container_width=True
    )

    ranking_exibir = ranking.sort_values(
    "faturamento",
    ascending=False
    ).copy()

    ranking_exibir["faturamento"] = (
        ranking_exibir["faturamento"]
        .map("R$ {:,.2f}".format)
    )

    ranking_exibir["ticket_medio"] = (
        ranking_exibir["ticket_medio"]
        .map("R$ {:,.2f}".format)
    )

    st.dataframe(
        ranking_exibir,
        use_container_width=True,
        hide_index=True
    )
    

    # =========================================
    # FATURAMENTO POR MÊS
    # =========================================

    fat_mes = (
    df_pedidos
    .groupby(["mes_ano_data", "mes_ano"])[COLUNA_VALOR]
    .sum()
    .reset_index()
    .sort_values("mes_ano_data")
)

    # crescimento %
    fat_mes["crescimento_%"] = (
        fat_mes[COLUNA_VALOR]
        .pct_change()
        * 100
    )

    fat_mes["crescimento_%"] = (
        fat_mes["crescimento_%"]
        .fillna(0)
        .round(1)
    )

    # média móvel
    fat_mes["media_movel"] = (
        fat_mes[COLUNA_VALOR]
        .rolling(3)
        .mean()
    )

    fig_fat = px.bar(
        fat_mes,
        x="mes_ano",
        y=COLUNA_VALOR,
        title="📈 Faturamento por Mês",
        text_auto=True,
        hover_data={
            COLUNA_VALOR: ":,.2f",
            "crescimento_%": True,
            "media_movel": ":,.2f"
        }
    )

    # linha tendência
    fig_fat.add_scatter(
        x=fat_mes["mes_ano"],
        y=fat_mes["media_movel"],
        mode="lines+markers",
        name="Média Móvel 3M"
    )

    st.plotly_chart(
        fig_fat,
        use_container_width=True
    )

    # =========================================
    # TICKET MÉDIO POR MÊS
    # =========================================

    ticket_mes = (
    df_pedidos
    .groupby(["mes_ano_data", "mes_ano"])
    .agg(
        faturamento=(COLUNA_VALOR, "sum"),
        pedidos=(COLUNA_PEDIDO, "nunique")
    )
    .reset_index()
    .sort_values("mes_ano_data")
    )

    ticket_mes["ticket_medio"] = (
        ticket_mes["faturamento"] /
        ticket_mes["pedidos"]
    )

    # crescimento ticket
    ticket_mes["crescimento_ticket_%"] = (
        ticket_mes["ticket_medio"]
        .pct_change()
        * 100
    )

    ticket_mes["crescimento_ticket_%"] = (
        ticket_mes["crescimento_ticket_%"]
        .fillna(0)
        .round(1)
    )

    # média móvel ticket
    ticket_mes["media_ticket"] = (
        ticket_mes["ticket_medio"]
        .rolling(3)
        .mean()
    )

    fig_ticket = px.bar(
        ticket_mes,
        x="mes_ano",
        y="ticket_medio",
        title="📦 Ticket Médio por Mês",
        text_auto=True,
        hover_data={
            "ticket_medio": ":,.2f",
            "pedidos": True,
            "crescimento_ticket_%": True,
            "media_ticket": ":,.2f"
        }
    )

    # linha média móvel
    fig_ticket.add_scatter(
        x=ticket_mes["mes_ano"],
        y=ticket_mes["media_ticket"],
        mode="lines+markers",
        name="Média Móvel Ticket"
    )

    st.plotly_chart(
        fig_ticket,
        use_container_width=True
    )

    # =========================================
    # ORÇADO X PEDIDO
    # =========================================

    # ORÇADO
    orcado_mes = (
        df_orcamentos
        .groupby(["mes_ano_data", "mes_ano"])[COLUNA_VALOR]
        .sum()
        .reset_index()
        .sort_values("mes_ano_data")
    )

    orcado_mes.rename(
        columns={
            COLUNA_VALOR: "valor_orcado"
        },
        inplace=True
    )

    # PEDIDO
    pedido_mes = (
        df_pedidos
        .groupby(["mes_ano_data", "mes_ano"])[COLUNA_VALOR]
        .sum()
        .reset_index()
        .sort_values("mes_ano_data")
    )

    pedido_mes.rename(
        columns={
            COLUNA_VALOR: "valor_pedido"
        },
        inplace=True
    )

    # MERGE
    comparativo = pd.merge(
        orcado_mes,
        pedido_mes,
        on=["mes_ano_data", "mes_ano"],
        how="outer"
    ).fillna(0)

    comparativo = comparativo.sort_values("mes_ano_data")

    # MELT
    comparativo_melt = comparativo.melt(
        id_vars="mes_ano",
        value_vars=["valor_orcado", "valor_pedido"],
        var_name="tipo",
        value_name="valor"
    )

    # GRÁFICO
    fig_comp = px.bar(
        comparativo_melt,
        x="mes_ano",
        y="valor",
        color="tipo",
        barmode="group",
        title="Orçado x Pedido por Mês",
        text_auto=True
    )

    st.plotly_chart(
        fig_comp,
        use_container_width=True
    )

    
    # =========================================
    # TABELA
    # =========================================

    st.subheader("Dados")

    df_exibir = df_filtrado.copy()

    # formatar valor
    df_exibir[COLUNA_VALOR] = (
        df_exibir[COLUNA_VALOR]
        .fillna(0)
        .map("R$ {:,.2f}".format)
    )

    # formatar data
    df_exibir[COLUNA_DATA] = (
        pd.to_datetime(df_exibir[COLUNA_DATA])
        .dt.strftime("%d/%m/%Y")
    )

    st.dataframe(
        df_exibir,
        use_container_width=True,
        hide_index=True,
        height=500
    )

    

