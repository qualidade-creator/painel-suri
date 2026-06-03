import streamlit as st
import pandas as pd

def tela_api_produtos():

    url = (
        "https://docs.google.com/spreadsheets/d/"
        "1rz0pmsqYWy8W83bLAn_uhvIIG69rFwQBUGrXOxIdL_o"
        "/export?format=csv&gid=0"
    )

    df = pd.read_csv(url)

    st.json(
        df.to_dict(
            orient="records"
        )
    )