from fastapi import FastAPI
import pandas as pd

app = FastAPI()

@app.get("/produtos")
def produtos():

    url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTPp5-ZfpJFYC22BlBkdcZ2xavyge91HPP0VK8JbbGfJfW6KNxNv591elxgmPoWBNBZAlmGeYZ_0sXA/pub?gid=0&single=true&output=csv"

    df = pd.read_csv(url)

    return df.to_dict(orient="records")

