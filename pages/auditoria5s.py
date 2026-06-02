import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from collections import Counter
from wordcloud import WordCloud
import matplotlib.pyplot as plt
import re

def tela_auditoria():

    st.title("🔎 Dashboard Auditoria 5S")

    # ==================================================
    # GOOGLE SHEETS
    # ==================================================

    url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQjZoVNM-gbApKA5grM1AYVX9NRt4TX6ICbBx2qyeMOufoVIvfMAFKSa6q_mBzqgQtHPT1cQk26kr0M/pub?output=csv"

    # ==================================================
    # CACHE
    # ==================================================

    @st.cache_data(ttl=60)
    def carregar():

        df = pd.read_csv(
            url,
            sep=None,
            engine="python",
            on_bad_lines="skip"
        )

        df.columns = (
            df.columns
            .str.strip()
            .str.lower()
            .str.replace(" ", "_")
        )

        return df

    df = carregar()

    # ==================================================
    # COLUNAS
    # ==================================================

    COLUNA_DATA = "carimbo_de_data/hora"
    COLUNA_UNIDADE = "unidade"
    COLUNA_AUDITOR = "auditor_(a)"
    COLUNA_OBS = "observação"

    # ==================================================
    # DATA
    # ==================================================

    df[COLUNA_DATA] = pd.to_datetime(
        df[COLUNA_DATA],
        dayfirst=True,
        errors="coerce"
    )

    # ==================================================
    # MÊS/ANO
    # ==================================================

    df["mes_ano_data"] = (
        df[COLUNA_DATA]
        .dt.to_period("M")
        .dt.to_timestamp()
    )

    df["mes_ano"] = (
        df["mes_ano_data"]
        .dt.strftime("%m/%Y")
    )

    # ==================================================
    # PERGUNTAS
    # ==================================================

    colunas_fixas = [
        COLUNA_DATA,
        COLUNA_UNIDADE,
        COLUNA_AUDITOR,
        COLUNA_OBS,
        "mes_ano",
        "mes_ano_data"
    ]

    colunas_perguntas = [
        c for c in df.columns
        if c not in colunas_fixas
    ]

    # ==================================================
    # CONVERTER NOTAS EM %
    # ==================================================

    def converter_nota(valor):

        valor = str(valor).lower()

        if "excelente" in valor:
            return 100

        elif "bom" in valor:
            return 66.66

        elif "ruim" in valor:
            return 33.33

        return None

    for coluna in colunas_perguntas:

        df[coluna] = df[coluna].apply(converter_nota)

    # ==================================================
    # MÉDIA FINAL
    # ==================================================

    df["score_5s"] = (
        df[colunas_perguntas]
        .mean(axis=1)
    )

    # ==================================================
    # FILTROS
    # ==================================================

    st.sidebar.header("Filtros")

    unidades = st.sidebar.multiselect(
        "Unidade",
        options=df[COLUNA_UNIDADE].dropna().unique(),
        default=df[COLUNA_UNIDADE].dropna().unique()
    )

    df_filtrado = df[
        df[COLUNA_UNIDADE].isin(unidades)
    ]

    # ==================================================
    # KPI PRINCIPAL
    # ==================================================

    media_geral = round(
        df_filtrado["score_5s"].mean(),
        1
    )

    total_auditorias = len(df_filtrado)

    meta_5s = 85

    conformidade = round(
        (media_geral / meta_5s) * 100,
        1
    )

    col1, col2, col3 = st.columns(3)

    col1.metric(
        "🏆 Score Médio 5S",
        f"{media_geral}%"
    )

    col2.metric(
        "📝 Auditorias",
        total_auditorias
    )

    col3.metric(
        "🎯 Conformidade Meta",
        f"{conformidade}%"
    )

    st.divider()

    # ==================================================
    # GAUGE CHART
    # ==================================================

    fig_gauge = go.Figure(go.Indicator(
        mode="gauge+number",
        value=media_geral,
        number={'suffix': "%"},
        title={'text': "Performance Geral 5S"},
        gauge={
            'axis': {'range': [0, 100]},
            'bar': {'color': "#1f77b4"},
            'steps': [
                {'range': [0, 50], 'color': "#ff4d4d"},
                {'range': [50, 80], 'color': "#ffd11a"},
                {'range': [80, 100], 'color': "#2ecc71"}
            ],
            'threshold': {
                'line': {'color': "black", 'width': 4},
                'thickness': 0.75,
                'value': meta_5s
            }
        }
    ))

    st.plotly_chart(
        fig_gauge,
        use_container_width=True
    )

    # ==================================================
    # MÉDIA POR UNIDADE
    # ==================================================

    media_unidade = (
        df_filtrado
        .groupby(COLUNA_UNIDADE)["score_5s"]
        .mean()
        .reset_index()
        .sort_values("score_5s", ascending=True)
    )

    fig_unidade = px.bar(
        media_unidade,
        x="score_5s",
        y=COLUNA_UNIDADE,
        orientation="h",
        text_auto=".1f",
        color="score_5s",
        color_continuous_scale="RdYlGn",
        title="Ranking 5S por Unidade"
    )

    fig_unidade.update_traces(
        texttemplate='%{text}%',
        textposition='outside'
    )

    fig_unidade.update_layout(
        xaxis_title="Score %",
        yaxis_title="",
        coloraxis_showscale=False
    )

    st.plotly_chart(
        fig_unidade,
        use_container_width=True
    )

    # ==================================================
    # EVOLUÇÃO MENSAL
    # ==================================================

    media_mes = (
        df_filtrado
        .groupby(["mes_ano_data", "mes_ano"])["score_5s"]
        .mean()
        .reset_index()
        .sort_values("mes_ano_data")
    )

    fig_mes = px.line(
        media_mes,
        x="mes_ano",
        y="score_5s",
        markers=True,
        text="score_5s",
        title="Evolução Mensal 5S"
    )

    fig_mes.update_traces(
        texttemplate='%{text:.1f}%',
        textposition='top center',
        line=dict(width=4)
    )

    fig_mes.update_layout(
        yaxis_title="Score %",
        xaxis_title=""
    )

    st.plotly_chart(
        fig_mes,
        use_container_width=True
    )

    # ==================================================
    # TABELA DETALHADA
    # ==================================================

    st.subheader("📋 Resultado das Auditorias")

    tabela = df_filtrado[
        [
            COLUNA_DATA,
            COLUNA_UNIDADE,
            COLUNA_AUDITOR,
            "score_5s"
        ]
    ].copy()

    tabela["score_5s"] = (
        tabela["score_5s"]
        .round(1)
        .astype(str) + "%"
    )

    st.dataframe(
        tabela,
        use_container_width=True,
        hide_index=True
    )

    # ==================================================
    # MAPA ÁREAS
    # ==================================================

    MAPA_AREAS = {

        "1": "Comercial",
        "2": "Administrativo",
        "3": "Almoxarifado",
        "4": "DML",
        "5": "2º Piso",
        "6": "Vestiário",
        "7": "Refeitório",
        "8": "Marketing",
        "9": "Geral",
        "10": "3º Piso",
        "11": "Sala Logística",
        "12": "Sala Diretoria",
        "13": "Área Externa"
    }

    

    # ==================================================
    # EXTRAÇÃO DOS PROBLEMAS
    # ==================================================

    dados_problemas = []

    for obs in df_filtrado[COLUNA_OBS].dropna():

        texto = str(obs)

        # ==========================================
        # CAPTURA:
        # (2)Luminaria solta do forro COZINHA
        # ==========================================

        ocorrencias = re.findall(

            r"\((\d+)\)([^;]+)",

            texto
        )

        # ==========================================
        # PROCESSA
        # ==========================================

        for codigo, problema in ocorrencias:

            area = MAPA_AREAS.get(
                codigo,
                "Não Identificada"
            )

            problema = (

                problema

                .strip()

                .replace(";", "")

                .replace("\n", " ")
            )

            # ======================================
            # NORMALIZA TEXO
            # ======================================

            problema = (

                problema

                .lower()

                .strip()

                .capitalize()
            )

            # ======================================
            # SALVA
            # ======================================

            if problema != "":

                dados_problemas.append({

                    "Área": area,

                    "Problema": problema
                })

    # ==================================================
    # DATAFRAME
    # ==================================================

    df_problemas = pd.DataFrame(dados_problemas)

    # ==================================================
    # AGRUPA PROBLEMAS
    # ==================================================

    pareto = (

        df_problemas

        .groupby(["Área", "Problema"])

        .size()

        .reset_index(name="Qtd")

        .sort_values(
            "Qtd",
            ascending=False
        )
    )

    # ==================================================
    # PARETO DE ÁREAS
    # ==================================================

    st.subheader("📊 Pareto de Problemas por Área")

    # ==========================================
    # AGRUPA ÁREAS
    # ==========================================

    pareto_areas = (

        df_problemas

        .groupby("Área")

        .size()

        .reset_index(name="Qtd")

        .sort_values(
            "Qtd",
            ascending=False
        )
    )

    # ==========================================
    # % ACUMULADA
    # ==========================================

    pareto_areas["Perc_Acumulado"] = (

        pareto_areas["Qtd"].cumsum()

        / pareto_areas["Qtd"].sum()

    ) * 100

    # ==========================================
    # FIGURA
    # ==========================================

    fig_area = go.Figure()

    # ==========================================
    # BARRAS
    # ==========================================

    fig_area.add_trace(

        go.Bar(

            x=pareto_areas["Área"],

            y=pareto_areas["Qtd"],

            text=pareto_areas["Qtd"],

            textposition="outside",

            marker=dict(
                color="#3B82F6"
            ),

            name="Ocorrências"
        )
    )

    # ==========================================
    # LINHA ACUMULADA
    # ==========================================

    fig_area.add_trace(

        go.Scatter(

            x=pareto_areas["Área"],

            y=pareto_areas["Perc_Acumulado"],

            mode="lines+markers",

            yaxis="y2",

            line=dict(
                color="#22C55E",
                width=3
            ),

            name="% Acumulado"
        )
    )

    # ==========================================
    # LINHA 80%
    # ==========================================

    fig_area.add_hline(

        y=80,

        yref="y2",

        line_dash="dash",

        line_color="red"
    )

    # ==========================================
    # LAYOUT
    # ==========================================

    fig_area.update_layout(

        title="Pareto de Não Conformidades por Área",

        template="plotly_dark",

        height=600,

        paper_bgcolor="#0E1117",

        plot_bgcolor="#0E1117",

        font=dict(
            color="white"
        ),

        margin=dict(
            l=20,
            r=20,
            t=60,
            b=80
        ),

        xaxis=dict(
            title=""
        ),

        yaxis=dict(
            title="Ocorrências"
        ),

        yaxis2=dict(

            title="% Acumulado",

            overlaying="y",

            side="right",

            range=[0, 100]
        )
    )

    # ==========================================
    # EXIBE
    # ==========================================

    st.plotly_chart(

        fig_area,

        use_container_width=True
    )

    # ==================================================
    # FILTRO ÁREA
    # ==================================================

    st.subheader("📊 Pareto de Problemas")

    areas = sorted(
        pareto["Área"].unique()
    )

    area_selecionada = st.selectbox(

        "Selecione a Área",

        areas
    )

    pareto_area = pareto[
        pareto["Área"] == area_selecionada
    ]

    # ==================================================
    # TOP 15
    # ==================================================

    pareto_area = pareto_area.head(15)

    # ==================================================
    # % ACUMULADA
    # ==================================================

    pareto_area["Perc_Acumulado"] = (

        pareto_area["Qtd"].cumsum()

        / pareto_area["Qtd"].sum()

    ) * 100

    # ==================================================
    # FIGURA
    # ==================================================

    fig = go.Figure()

    # ==========================================
    # BARRAS
    # ==========================================

    fig.add_trace(

        go.Bar(

            x=pareto_area["Problema"],

            y=pareto_area["Qtd"],

            text=pareto_area["Qtd"],

            textposition="outside",

            marker=dict(
                color="#3B82F6"
            ),

            name="Ocorrências"
        )
    )

    # ==========================================
    # LINHA ACUMULADA
    # ==========================================

    fig.add_trace(

        go.Scatter(

            x=pareto_area["Problema"],

            y=pareto_area["Perc_Acumulado"],

            mode="lines+markers",

            yaxis="y2",

            line=dict(
                color="#22C55E",
                width=3
            ),

            name="% Acumulado"
        )
    )

    # ==========================================
    # LINHA 80%
    # ==========================================

    fig.add_hline(

        y=80,

        yref="y2",

        line_dash="dash",

        line_color="red"
    )

    # ==================================================
    # LAYOUT
    # ==================================================

    fig.update_layout(

        title=f"Pareto - {area_selecionada}",

        template="plotly_dark",

        height=650,

        paper_bgcolor="#0E1117",

        plot_bgcolor="#0E1117",

        font=dict(
            color="white"
        ),

        margin=dict(
            l=20,
            r=20,
            t=60,
            b=100
        ),

        xaxis=dict(
            tickangle=-25,
            title=""
        ),

        yaxis=dict(
            title="Ocorrências"
        ),

        yaxis2=dict(

            title="% Acumulado",

            overlaying="y",

            side="right",

            range=[0, 100]
        )
    )

    # ==================================================
    # EXIBE
    # ==================================================

    st.plotly_chart(

        fig,

        use_container_width=True
    )