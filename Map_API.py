from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import re
import os
import time

app = FastAPI(title="Road Mapping API")

# ===== Models =====
class CPPair(BaseModel):
    CPP_1: str
    CPP_2: str
    CPC_1: str
    CPC_2: str

class RoutesRequest(BaseModel):
    pairs: List[CPPair]
    tomtom_key: Optional[str] = None
    delay_between_requests: Optional[float] = 0.2  # segundos

class RouteResult(BaseModel):
    CP_Partida: str
    Latitude_Partida: Optional[float]
    Longitude_Partida: Optional[float]
    CP_Chegada: str
    Latitude_Chegada: Optional[float]
    Longitude_Chegada: Optional[float]
    Dist_Result_km: Optional[float]
    Tempo_Result_min: Optional[float]
    error: Optional[str] = None

# ===== Helpers (copiados/adaptados do Maping.py) =====
def safe_val_dbl(s: str) -> Optional[float]:
    if s is None:
        return None
    s = str(s).replace('\xa0', ' ').strip().replace(',', '.')
    try:
        match = re.findall(r"[-+]?\d*\.\d+|\d+", s)
        return float(match[0]) if match else None
    except ValueError:
        return None

def get_coordinates(cp4: str, cp3: str):
    url = f"https://www.codigo-postal.pt/?cp4={cp4}&cp3={cp3}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None, None
        html = r.text
        pattern = re.compile(r'pull-right\s+gps[\s\S]*?([+-]?\d+\.\d+)[\s,]+([+-]?\d+\.\d+)', re.MULTILINE)
        matches = pattern.findall(html)
        if not matches:
            return None, None
        latitudes = [safe_val_dbl(lat) for lat, _ in matches if safe_val_dbl(lat) is not None]
        longitudes = [safe_val_dbl(lon) for _, lon in matches if safe_val_dbl(lon) is not None]
        if not latitudes or not longitudes:
            return None, None
        return sum(latitudes) / len(latitudes), sum(longitudes) / len(longitudes)
    except Exception:
        return None, None

def calculate_distance(lat_orig, lon_orig, lat_dest, lon_dest, api_key):
    if None in (lat_orig, lon_orig, lat_dest, lon_dest):
        return None, None
    api_url = (
        f"https://api.tomtom.com/routing/1/calculateRoute/"
        f"{lat_orig},{lon_orig}:{lat_dest},{lon_dest}/json"
        f"?key={api_key}&maxAlternatives=5&sectionType=traffic&sectionType=urban"
        f"&extendedRouteRepresentation=travelTime&travelMode=truck"
    )
    try:
        r = requests.get(api_url, timeout=10)
        if r.status_code != 200:
            return None, None
        data = r.json()
        if "routes" in data and len(data["routes"]) > 0:
            summary = data["routes"][0].get("summary", {})
            distance_km = summary.get("lengthInMeters")
            time_s = summary.get("travelTimeInSeconds")
            if distance_km is None or time_s is None:
                return None, None
            return round(distance_km / 1000, 2), round(time_s / 60, 2)
        return None, None
    except Exception:
        return None, None

# ===== Endpoints =====
@app.get("/")
def root():
    return {"ok": True, "note": "POST /routes com payload { pairs: [...] }"}

@app.post("/routes", response_model=List[RouteResult])
def compute_routes(req: RoutesRequest):
    tomtom_key = req.tomtom_key or os.getenv("TOMTOM_KEY")
    if not tomtom_key:
        raise HTTPException(status_code=400, detail="TomTom API key required (tomtom_key or TOMTOM_KEY env)")

    results = []
    for p in req.pairs:
        cp_partida = f"{str(p.CPP_1).zfill(4)}-{str(p.CPP_2).zfill(3)}"
        cp_chegada = f"{str(p.CPC_1).zfill(4)}-{str(p.CPC_2).zfill(3)}"

        lat_p, lon_p = get_coordinates(p.CPP_1, p.CPP_2)
        lat_c, lon_c = get_coordinates(p.CPC_1, p.CPC_2)

        if lat_p is None or lat_c is None:
            dist_km, time_min = None, None
            err = "Sem coordenadas para partida ou chegada"
        else:
            dist_km, time_min = calculate_distance(lat_p, lon_p, lat_c, lon_c, tomtom_key)
            err = None
        results.append(RouteResult(
            CP_Partida=cp_partida,
            Latitude_Partida=lat_p,
            Longitude_Partida=lon_p,
            CP_Chegada=cp_chegada,
            Latitude_Chegada=lat_c,
            Longitude_Chegada=lon_c,
            Dist_Result_km=dist_km,
            Tempo_Result_min=time_min,
            error=err
        ))
        if req.delay_between_requests:
            time.sleep(req.delay_between_requests)
    return results