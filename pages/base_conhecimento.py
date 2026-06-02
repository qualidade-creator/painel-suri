import streamlit as st
import pandas as pd
import json
import unicodedata
import os
import gspread
from oauth2client.service_account import (
    ServiceAccountCredentials
)

from gspread_dataframe import (
    set_with_dataframe
)

PASTA_BASE = "bases"

os.makedirs(PASTA_BASE, exist_ok=True)

# ==================================
# GOOGLE SHEETS
# ==================================

def atualizar_google_sheets(df):

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]

    credenciais = dict(
    st.secrets["gcp_service_account"]
    )

    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        credenciais,
        scope
    )

    client = gspread.authorize(
        creds
    )

    planilha = client.open(
        "Base Conhecimento Suri"
    )

    aba = planilha.worksheet(
        "Produtos"
    )

    aba.clear()

    set_with_dataframe(
        aba,
        df
    )

# ==================================
# NORMALIZAÇÃO
# ==================================

def normalizar(texto):
    return (
        unicodedata
        .normalize('NFKD', str(texto))
        .encode('ASCII', 'ignore')
        .decode('ASCII')
        .lower()
        .strip()
    )

def normalizar_colunas(df):
    df.columns = [normalizar(col) for col in df.columns]
    return df

# ==================================
# LEITOR MD
# ==================================

def ler_md_tabela(upload):

    conteudo = upload.read().decode("utf-8")

    linhas = []

    for linha in conteudo.splitlines():

        linha = linha.strip()

        if (
            not linha
            or linha.startswith("#")
            or linha.startswith("|---")
        ):
            continue

        if "|" in linha:
            linhas.append(
                [c.strip() for c in linha.strip("|").split("|")]
            )

    return pd.DataFrame(
        linhas[1:],
        columns=linhas[0]
    )

# ==================================
# TELA
# ==================================

def tela_base_conhecimento():

    st.title("📚 Base de Conhecimento")

    arquivo_md = st.file_uploader(
        "Upload da base MD",
        type=["md"]
    )

    arquivo_excel = st.file_uploader(
        "Upload da tabela de produtos",
        type=["xlsx"]
    )

    if arquivo_md and arquivo_excel:

        try:

            # ========================
            # LEITURA
            # ========================

            df_precos = ler_md_tabela(
                arquivo_md
            )

            df_produtos = pd.read_excel(
                arquivo_excel
            )

            # ========================
            # NORMALIZAR
            # ========================

            df_precos = normalizar_colunas(
                df_precos
            )

            df_produtos = normalizar_colunas(
                df_produtos
            )

            # ========================
            # RENOMEAR
            # ========================

            df_precos = df_precos.rename(
                columns={
                    "preco venda": "PrecoVenda",
                    "estoque atual": "Estoque",
                    "codigo do produto": "Codigo",
                    "produto": "Descricao"
                }
            )

            df_produtos = df_produtos.rename(
                columns={
                    "cod. origem": "Codigo",
                    "grupo": "Grupo",
                    "subgrupo": "SubGrupo"
                }
            )

            # ========================
            # LIMPEZA
            # ========================

            colunas_precos = [
            "Codigo",
            "Descricao",
            "PrecoVenda",
            "Estoque"
        ]

            for coluna in colunas_precos:
                if coluna not in df_precos.columns:
                    raise Exception(
                        f"Coluna não encontrada: {coluna}"
                    )

                df_precos["Codigo"] = (
                    df_precos["Codigo"]
                    .astype(str)
                    .str.strip()
                )

                colunas_produtos = [
                "Codigo",
                "Grupo",
                "SubGrupo"
            ]

            for coluna in colunas_produtos:
                if coluna not in df_produtos.columns:
                    raise Exception(
                        f"Coluna não encontrada: {coluna}"
            )

            df_produtos["Codigo"] = (
                df_produtos["Codigo"]
                .astype(str)
                .str.strip()
            )

            df_precos["Estoque"] = pd.to_numeric(
                df_precos["Estoque"]
                .astype(str)
                .str.replace(",", "."),
                errors="coerce"
            ).fillna(0)

            # ========================
            # AGRUPAR
            # ========================

            df_precos = (
                df_precos
                .groupby(
                    [
                        "Codigo",
                        "Descricao",
                        "PrecoVenda"
                    ],
                    as_index=False
                )["Estoque"]
                .sum()
            )

            df_precos.rename(
                columns={
                    "Estoque":"EstoqueTotal"
                },
                inplace=True
            )

            # ========================
            # MERGE
            # ========================

            df_final = pd.merge(
                df_precos,
                df_produtos[
                    [
                        "Codigo",
                        "Grupo",
                        "SubGrupo"
                    ]
                ],
                on="Codigo",
                how="left"
            )

            df_final["Grupo"] = (
                df_final["Grupo"]
                .fillna("NAO CLASSIFICADO")
            )

            df_final["SubGrupo"] = (
                df_final["SubGrupo"]
                .fillna("NAO CLASSIFICADO")
            )

            # Atualiza Google Sheets
            from datetime import datetime
            df_final["DataAtualizacao"] = (
                datetime.now()
                .strftime("%d/%m/%Y %H:%M:%S")
            )

            # ========================================
            # TRATAR CÓDIGOS INICIADOS COM "+"
            # ========================================

            df_final["Codigo"] = (
                df_final["Codigo"]
                .astype(str)
                .apply(
                    lambda x: f"'{x}"
                    if x.startswith("+")
                    else x
                )
            )

            atualizar_google_sheets(
                df_final
            )

            st.success(
            f"✅ {len(df_final):,} produtos enviados para o Google Sheets"
            )

            
            # ========================
            # EXPORTAR JSON
            # ========================

            json_path = os.path.join(
                PASTA_BASE,
                "produtos.json"
            )

            df_final.to_json(
                json_path,
                orient="records",
                force_ascii=False,
                
            )

            # ========================
            # ESTATÍSTICAS DA BASE
            # ========================

            st.subheader("📊 Estatísticas da Base")

            col1, col2, col3, col4 = st.columns(4)

            col1.metric(
                "Produtos",
                len(df_final)
            )

            col2.metric(
                "Grupos",
                df_final["Grupo"].nunique()
            )

            col3.metric(
                "Subgrupos",
                df_final["SubGrupo"].nunique()
            )

            col4.metric(
                "Estoque Total",
                int(df_final["EstoqueTotal"].sum())
            )

            st.success(
                "Base atualizada com sucesso!"
            )

            url_base = (
                "https://docs.google.com/spreadsheets/d/"
                "1rz0pmsqYWy8W83bLAn_uhvIIG69rFwQBUGrXOxIdL_o"
                "/export?format=csv&gid=0"
            )

            st.code(url_base)  
            
            with open(json_path, "rb") as f:

                st.download_button(
                    "📦 Baixar JSON",
                    data=f,
                    file_name="produtos.json",
                    mime="application/json"
                )

            st.dataframe(
                df_final,
                use_container_width=True,
                height=600
            )
            

        except Exception as erro:

            st.error(str(erro))