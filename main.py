import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
import pandas as pd
import requests
import re
from io import StringIO
import uvicorn
from typing import Tuple, Optional, Dict

# =============================================================================
# Configurações para ambiente Render / produção
# =============================================================================

# Diretório base do projeto (onde está o main.py)
BASE_DIR = Path(__file__).resolve().parent

# API TomTom a partir de variável de ambiente (obrigatória em Render)
API_KEY_TOMTOM = os.getenv("API_KEY_TOMTOM")
if not API_KEY_TOMTOM:
    raise RuntimeError(
        "API_KEY_TOMTOM não está definida. "
        "Em Render, cria uma Environment Variable chamada API_KEY_TOMTOM."
    )

# Caminho do Excel:
# 1) Se existir CAMINHO_EXCEL nas env vars, usa esse
# 2) Caso contrário, usa CEP2.xlsx na mesma pasta do main.py
CAMINHO_EXCEL = Path(
    os.getenv("CAMINHO_EXCEL", str(BASE_DIR / "CEP2.xlsx"))
).resolve()

# Caches em memória: coordenadas por CEP e rotas por par de coordenadas+modo
coord_cache: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
route_cache: Dict[str, Tuple[Optional[float], Optional[float]]] = {}

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

app = FastAPI(
    title="API Distâncias CP",
    description="Calcula coordenadas e distâncias a partir de um Excel no servidor (com cache)",
    version="1.3.0",
)


# =============================================================================
# Funções utilitárias
# =============================================================================

def safe_val_dbl(s: str) -> Optional[float]:
    if pd.isna(s):
        return None
    s = str(s).replace("\xa0", " ").strip().replace(",", ".")
    match = re.findall(r"[-+]?\d*\.\d+|\d+", s)
    if not match:
        return None
    try:
        return float(match[0])
    except (ValueError, IndexError):
        return None


def normalize_cep(cep: str) -> str:
    """
    Normaliza um CEP para a forma 'XXXX-XXX' quando possível.
    Remove espaços e caracteres não numéricos. Se restarem 7 dígitos, formata com hífen.
    Retorna string vazia se não houver conteúdo útil.
    """
    if cep is None:
        return ""
    s = str(cep)
    digits = re.findall(r"\d+", s)
    if not digits:
        return s.strip()
    all_digits = "".join(digits)
    if len(all_digits) == 7:
        return f"{all_digits[:4]}-{all_digits[4:]}"
    return all_digits


def fetch_coordinates_from_site(cep: str) -> Tuple[Optional[float], Optional[float]]:
    """Busca as coordenadas no site codigo-postal.pt (fallback simples)."""
    try:
        url = f"https://www.codigo-postal.pt/?rua={cep}"
        r = session.get(url, timeout=8)
        if r.status_code != 200:
            return None, None
        html = r.text
        pattern = re.compile(
            r"pull-right\s+gps[\s\S]*?([+-]?\d+\.\d+)[\s,]+([+-]?\d+\.\d+)"
        )
        matches = pattern.findall(html)
        if not matches:
            return None, None
        latitudes = [
            safe_val_dbl(lat)
            for lat, _ in matches
            if safe_val_dbl(lat) is not None
        ]
        longitudes = [
            safe_val_dbl(lon)
            for _, lon in matches
            if safe_val_dbl(lon) is not None
        ]
        if not latitudes or not longitudes:
            return None, None
        avg_lat = sum(latitudes) / len(latitudes)
        avg_lon = sum(longitudes) / len(longitudes)
        return avg_lat, avg_lon
    except Exception as e:
        print(f"⚠ Erro ao buscar {cep}: {e}")
        return None, None


def get_coordinates_for_cep(cep: str) -> Tuple[Optional[float], Optional[float]]:
    """Retorna coordenadas a partir do cache; se não existir, busca e guarda no cache."""
    cep_norm = normalize_cep(cep)

    if cep_norm in coord_cache:
        print(f"[CACHE HIT] {cep_norm} -> {coord_cache[cep_norm]}")
        return coord_cache[cep_norm]

    print(f"[CACHE MISS] {cep_norm} -> A obter coordenadas...")
    lat, lon = fetch_coordinates_from_site(cep_norm)
    coord_cache[cep_norm] = (lat, lon)
    print(f"[CACHE STORE] {cep_norm} -> {lat}, {lon}")
    return lat, lon


def calculate_distance(
    lat_orig,
    lon_orig,
    lat_dest,
    lon_dest,
    api_key,
    travel_mode: str,
) -> Tuple[Optional[float], Optional[float]]:
    """Calcula distância/tempo via TomTom com cache por par de coordenadas+modo."""
    if None in (lat_orig, lon_orig, lat_dest, lon_dest):
        return None, None

    # Validar que são floats válidos
    try:
        lat_orig = float(lat_orig)
        lon_orig = float(lon_orig)
        lat_dest = float(lat_dest)
        lon_dest = float(lon_dest)
    except (ValueError, TypeError):
        return None, None

    key = f"{lat_orig},{lon_orig}:{lat_dest},{lon_dest}:{travel_mode}"
    if key in route_cache:
        print(f"[ROUTE CACHE HIT] {key} -> {route_cache[key]}")
        return route_cache[key]

    print(f"[ROUTE CACHE MISS] {key} -> a chamar API TomTom...")

    api_url = (
        "https://api.tomtom.com/routing/1/calculateRoute/"
        f"{lat_orig},{lon_orig}:{lat_dest},{lon_dest}/json"
        f"?key={api_key}&travelMode={travel_mode}"
    )
    try:
        r = session.get(api_url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "routes" in data and len(data["routes"]) > 0:
                summary = data["routes"][0]["summary"]
                distance_km = round(summary.get("lengthInMeters", 0) / 1000, 2)
                time_minutes = round(
                    summary.get("travelTimeInSeconds", 0) / 60, 2
                )
                route_cache[key] = (distance_km, time_minutes)
                print(
                    f"[ROUTE CACHE STORE] {key} -> "
                    f"{distance_km} km, {time_minutes} min"
                )
                return distance_km, time_minutes
    except Exception as e:
        print(f"⚠ Erro ao calcular rota: {e}")

    route_cache[key] = (None, None)
    print(f"[ROUTE CACHE STORE-FAIL] {key} -> (None, None)")
    return None, None


def validate_cep_format(cep: str) -> bool:
    """Valida formatos típicos de CEP PT: '1234-567' ou 7 dígitos '1234567'."""
    if not isinstance(cep, str):
        return False
    cep = cep.strip()
    return bool(
        re.match(r"^\d{4}-\d{3}$", cep) or
        re.match(r"^\d{7}$", cep)
    )


# =============================================================================
# Endpoints da API
# =============================================================================

@app.get("/")
async def root():
    return {
        "message": "API Distâncias CP (cache ativo, pronta para Render)",
        "excel_path": str(CAMINHO_EXCEL),
    }


@app.get("/debug/cache")
async def debug_cache():
    return {
        "coord_cache_size": len(coord_cache),
        "route_cache_size": len(route_cache),
        "route_cache_keys": list(route_cache.keys()),
    }


@app.get("/distancias", response_class=PlainTextResponse)
async def get_distancias(
    modo: str = Query("car", description="Modo de transporte: car, truck, van"),
    force_reload: bool = Query(
        False,
        description="Se true limpa os caches em memória e força recarga",
    ),
):
    modo = modo.lower()
    if modo not in ("car", "truck", "van"):
        raise HTTPException(
            status_code=400,
            detail="Modo inválido. Use car, truck ou van.",
        )

    # Se solicitado, limpar caches
    if force_reload:
        coord_cache.clear()
        route_cache.clear()
        print("[API] force_reload=true: caches limpos (coord_cache, route_cache)")

    # --- LER EXCEL ---
    if not CAMINHO_EXCEL.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"Ficheiro Excel não encontrado em {CAMINHO_EXCEL}",
        )

    try:
        with pd.ExcelFile(CAMINHO_EXCEL) as xls:
            sheet_to_use = None

            # 1) Tentar folha "cp"
            for name in xls.sheet_names:
                if name.strip().lower() == "cp":
                    sheet_to_use = name
                    break

            # 2) Se não encontrar "cp", tentar detectar por colunas
            if sheet_to_use is None:
                for name in xls.sheet_names:
                    try:
                        df_tmp = pd.read_excel(xls, sheet_name=name, nrows=5)
                    except Exception:
                        continue

                    cols = [str(c).strip().lower() for c in df_tmp.columns]

                    has_origem = any(
                        any(k in c for k in ("cep origem", "cep_origem", "orig", "partida"))
                        for c in cols
                    )
                    has_dest = any(
                        any(k in c for k in ("cep destin", "cep_destin", "dest", "chegada"))
                        for c in cols
                    )

                    if has_origem and has_dest:
                        sheet_to_use = name
                        break

            # 3) Ler a folha seleccionada ou fallback para a primeira
            if sheet_to_use:
                df = pd.read_excel(xls, sheet_name=sheet_to_use)
            else:
                df = pd.read_excel(xls, sheet_name=0)

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao carregar Excel: {str(e)}",
        )

    # Suportar dois nomes de colunas possíveis
    col_origem = None
    col_destino = None

    if "CEP Origem" in df.columns and "CEP Destin" in df.columns:
        col_origem = "CEP Origem"
        col_destino = "CEP Destin"
    elif "CP Partida" in df.columns and "CP Chegada" in df.columns:
        col_origem = "CP Partida"
        col_destino = "CP Chegada"
    else:
        cols_lidos = ", ".join([str(c) for c in df.columns])
        raise HTTPException(
            status_code=400,
            detail=(
                "O ficheiro deve conter as colunas: "
                "'CEP Origem'+'CEP Destin' ou 'CP Partida'+'CP Chegada'. "
                f"Colunas encontradas: {cols_lidos}"
            ),
        )

    # Seleciona apenas as colunas relevantes e limpa strings vazias (ex.: células com espaços)
    df_subset = df[[col_origem, col_destino]].copy()
    df_subset[col_origem] = (
        df_subset[col_origem].astype(str).str.strip().replace({"": pd.NA})
    )
    df_subset[col_destino] = (
        df_subset[col_destino].astype(str).str.strip().replace({"": pd.NA})
    )
    before_count = len(df_subset)
    df = df_subset.dropna(how="any").copy()
    after_count = len(df)
    print(f"[API] Linhas originais: {before_count}, após limpeza e dropna: {after_count}")
    if df.empty:
        raise HTTPException(
            status_code=400,
            detail="Não há linhas válidas com cp partida e cp chegada.",
        )

    # Pré-calcula coordenadas para CEPs distintos (usa cache global)
    all_ceps = pd.concat(
        [df[col_origem].astype(str), df[col_destino].astype(str)],
        ignore_index=True,
    )
    ceps_distintos = pd.Index(all_ceps).unique()

    print(f"\n[API] Processando {len(df)} linhas com {len(ceps_distintos)} CEPs distintos")

    for cep in ceps_distintos:
        lat, lon = get_coordinates_for_cep(cep)
        print(f"[API] {normalize_cep(cep):12} -> ({lat}, {lon})")

    print(f"[API] Cache coordenadas tem agora {len(coord_cache)} CEPS")

    # Funções helper para mapear coordenadas de forma segura
    def safe_get_lat(cep_val):
        cep_norm = normalize_cep(cep_val)
        coords = coord_cache.get(cep_norm, (None, None))
        return coords[0]

    def safe_get_lon(cep_val):
        cep_norm = normalize_cep(cep_val)
        coords = coord_cache.get(cep_norm, (None, None))
        return coords[1]

    # Mapeia coordenadas para cada linha de forma segura
    df["Orig_Lat"] = df[col_origem].astype(str).map(safe_get_lat)
    df["Orig_Lon"] = df[col_origem].astype(str).map(safe_get_lon)
    df["Dest_Lat"] = df[col_destino].astype(str).map(safe_get_lat)
    df["Dest_Lon"] = df[col_destino].astype(str).map(safe_get_lon)

    results = []
    skipped_count = 0

    for _, row in df.iterrows():
        cpp = normalize_cep(row[col_origem])
        cpc = normalize_cep(row[col_destino])
        lat_p = row["Orig_Lat"]
        lon_p = row["Orig_Lon"]
        lat_c = row["Dest_Lat"]
        lon_c = row["Dest_Lon"]

        # Verificar se coordenadas são válidas (não None, não NaN)
        try:
            lat_p = float(lat_p) if lat_p is not None else None
            lon_p = float(lon_p) if lon_p is not None else None
            lat_c = float(lat_c) if lat_c is not None else None
            lon_c = float(lon_c) if lon_c is not None else None
        except (ValueError, TypeError):
            lat_p = lon_p = lat_c = lon_c = None

        # Só incluir na tabela final linhas com CEPs válidos e coordenadas disponíveis
        if not (
            validate_cep_format(cpp)
            and validate_cep_format(cpc)
            and all(v is not None for v in [lat_p, lon_p, lat_c, lon_c])
        ):
            skipped_count += 1
            continue

        # Se os CEPS são exactamente iguais (forma normalizada), força distância e tempo a zero
        if cpp and cpc and cpp == cpc:
            distancia, tempo = 0.0, 0.0
        else:
            distancia, tempo = calculate_distance(
                lat_p, lon_p, lat_c, lon_c, API_KEY_TOMTOM, modo
            )

        results.append(
            {
                "CP Partida": cpp,
                "Latitude_Partida": lat_p,
                "Longitude_Partida": lon_p,
                "CP Chegada": cpc,
                "Latitude_Chegada": lat_c,
                "Longitude_Chegada": lon_c,
                "Distancia_km": f"{distancia} km",
                "Tempo_min": tempo,
            }
        )

    if skipped_count > 0:
        print(
            f"[API] Ignoradas {skipped_count} linhas sem coordenadas válidas "
            "ou com CEP inválido"
        )

    result_df = pd.DataFrame(results)
    csv_buffer = StringIO()
    result_df.to_csv(csv_buffer, index=False, sep=";")
    csv_buffer.seek(0)
    return csv_buffer.getvalue()


if __name__ == "__main__":
    # Para testes locais (Render vai usar o seu próprio comando)
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
