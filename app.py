import streamlit as st

from pages.vendas import tela_vendas
from pages.auditoria5s import tela_auditoria
from pages.base_conhecimento import tela_base_conhecimento
from pages.api_produtos import produtos
# =========================================
# CONFIG
# =========================================

st.set_page_config(
    page_title="Painel SURI",
    layout="wide"
)

# =========================================
# MENU
# =========================================

menu = st.sidebar.radio(
    "Menu Principal",
    [
        "📊 Dashboard Vendas",
        "🔎 Auditoria 5S",
        "📚 Base de Conhecimento",
        "📋 API Produtos"
    ]
)

# =========================================
# PÁGINAS
# =========================================

if menu == "📊 Dashboard Vendas":
    tela_vendas()

elif menu == "🔎 Auditoria 5S":
    tela_auditoria()

elif menu == "📚 Base de Conhecimento":
    tela_base_conhecimento()

elif menu == "📋 API Produtos":
    produtos()