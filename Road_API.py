from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
import pandas as pd
import requests
import re
from io import StringIO
import uvicorn

# Variáveis 
API_KEY_TOMTOM = "APIKey do TomTom"
CAMINHO_EXCEL = r"Caminho para o ficheiro xlsx com as colunas obrigatórias"

# Descrição da API através da framework FastAPI
app = FastAPI(
    title="API Distâncias CP",
    description="Calcula coordenadas e distâncias a partir de uma ligação ao Excel ",
    version="1.0.0",
)

# Algorítmo para ter acesso às coordenadas e distâncias

# Conversor de variável num para str
def safe_val_dbl(s: str):
    """Converte texto numérico para float de forma robusta."""
    if pd.isna(s):
        return None
    s = str(s).replace('\xa0', ' ').strip().replace(',', '.')
    try:
        match = re.findall(r"[-+]?\d*\.\d+|\d+", s)
        return float(match[0]) if match else None
    except ValueError:
        return None

# Busca das coordenadas através do CP (www.códigopostal.pt)
def get_coordinates(rua: str):
    """
    Obtém as coordenadas (lat, lon) de um código postal.
    Caso haja várias, devolve a média.
    """
    url = f"https://www.codigo-postal.pt/?rua={rua}"
    headers = {"User-Agent": "Mozilla/5.0"}

    # Validação do resultado
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None, None

        html = r.text

        pattern = re.compile(
            r'pull-right\s+gps[\s\S]*?([+-]?\d+\.\d+)[\s,]+([+-]?\d+\.\d+)',
            re.MULTILINE
        )
        matches = pattern.findall(html)

        if not matches:
            return None, None

        # Cálculo da média entre as coordenadas, no caso de ter mais de uma para o mesmo CP
        latitudes = [safe_val_dbl(lat) for lat, _ in matches if safe_val_dbl(lat) is not None]
        longitudes = [safe_val_dbl(lon) for _, lon in matches if safe_val_dbl(lon) is not None]

        if not latitudes or not longitudes:
            return None, None

        lat_media = sum(latitudes) / len(latitudes)
        lon_media = sum(longitudes) / len(longitudes)
        return lat_media, lon_media

    except Exception:
        return None, None


# ===============Configuração da API para a distância entre as coordenadas=================== #
 
def calculate_distance(lat_orig, lon_orig, lat_dest, lon_dest, api_key, travel_mode: str):
    """
    Calcula a distância e tempo entre dois pontos usando a API TomTom
    Retorna: (distância em km, tempo em minutos)
    """
    if None in (lat_orig, lon_orig, lat_dest, lon_dest):
        return None, None

    api_url = (
        f"https://api.tomtom.com/routing/1/calculateRoute/"
        f"{lat_orig},{lon_orig}:{lat_dest},{lon_dest}/json"
        f"?key={api_key}&travelMode={travel_mode}"
    )

    try:
        response = requests.get(api_url)
        if response.status_code == 200:
            data = response.json()
            if "routes" in data and len(data["routes"]) > 0:
                summary = data["routes"][0]["summary"]
                distance_km = summary["lengthInMeters"] / 1000
                time_minutes = summary["travelTimeInSeconds"] / 60
                return round(distance_km, 2), round(time_minutes, 2)
        return None, None
    except Exception:
        return None, None
    

# ====================================================
# ---- Configuração da Road_API para Excel ---- #
# ====================================================

@app.get("/")
async def root():
    return {
        "message": "Hello World"
    }

@app.get("/distancias", response_class=PlainTextResponse)
async def get_distancias(
    modo: str = Query("car", description="Modo de transporte: car, truck, van"),
):
    # Carrega o Excel a cada requisição para pegar novos CPs
    try:
        df = pd.read_excel(CAMINHO_EXCEL, sheet_name="CP")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao carregar Excel: {str(e)}")
    
    modo = modo.lower()
    if modo not in ("car", "truck", "van"):
        raise HTTPException(status_code=400, detail="Modo inválido. Use car, truck ou van.")

    required_cols = ["cp partida", "cp chegada"]
    if not set(required_cols).issubset(df.columns):
        cols_lidos = ", ".join([str(c) for c in df.columns])
        raise HTTPException(
            status_code=400,
            detail=f"O ficheiro deve conter as colunas: {', '.join(required_cols)}. Colunas encontradas: {cols_lidos}",
        )

    df = df[required_cols].dropna(how="any")

    if df.empty:
        raise HTTPException(status_code=400, detail="Não há linhas válidas com cp partida e cp chegada.")

    results = []

    for _, row in df.iterrows():
        cpp = row["cp partida"]
        cpc = row["cp chegada"]

        lat_p, lon_p = get_coordinates(str(cpp))
        lat_c, lon_c = get_coordinates(str(cpc))

        distancia, tempo = calculate_distance(
            lat_p, lon_p, lat_c, lon_c, API_KEY_TOMTOM, modo
        )
        
        pattern = r"^[0-9]{4}-[0-9]{3}$"  # Exemplo: valida formato XXXX-XXX
        if re.match(pattern, cpp and cpc):
            results.append({
                "CP Partida": f"{str(cpp).zfill(8)}",
                "Latitude_Partida": lat_p,
                "Longitude_Partida": lon_p,
                "CP Chegada": f"{str(cpc).zfill(8)}",
                "Latitude_Chegada": lat_c,
                "Longitude_Chegada": lon_c,
                "Distancia_km": f"{distancia} km",
                "Tempo_min": tempo,
            })
        else:
            results.append({
                "CP Partida": f"Verifique a existência de erros no seguinte CP: {str(cpp).zfill(8)}.",
                "Latitude_Partida": None,
                "Longitude_Partida": None,
                "CP Chegada": f"Verifique a existência de erros no seguinte CP: {str(cpc).zfill(8)}.",
                "Latitude_Chegada": None,
                "Longitude_Chegada": None,
                "Distancia_km": None,
                "Tempo_min": None,
            })
            
        result_df = pd.DataFrame(results)

    from io import StringIO
    csv_buffer = StringIO()
    result_df.to_csv(csv_buffer, index=False, sep=";")
    csv_buffer.seek(0)

    return csv_buffer.getvalue()

if __name__ == "__main__":
    uvicorn.run("Bapi:app", host="127.0.0.1", port=8000, reload=True)
