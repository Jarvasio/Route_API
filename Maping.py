import streamlit as st
import requests
from urllib import response
import pandas as pd
from io import BytesIO
import re

# 🔹 Função auxiliar: conversão segura de texto para float
def safe_val_dbl(s: str) -> float:
    """Converte texto numérico para float de forma robusta."""
    if pd.isna(s):
        return None
    s = str(s).replace('\xa0', ' ').strip().replace(',', '.')
    try:
        match = re.findall(r"[-+]?\d*\.\d+|\d+", s)
        return float(match[0]) if match else None
    except ValueError:
        return None

# =====================================================
# 🔹 Função principal: obter coordenadas de um CP4-CP3
# =====================================================
def get_coordinates(cp4: str, cp3: str):
    """
    Obtém as coordenadas (lat, lon) de um código postal.
    Caso haja várias, devolve a média.
    """
    url = f"https://www.codigo-postal.pt/?cp4={cp4}&cp3={cp3}"

    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None, None

        html = r.text

        # Expressão regular para capturar múltiplas coordenadas
        pattern = re.compile(
            r'pull-right\s+gps[\s\S]*?([+-]?\d+\.\d+)[\s,]+([+-]?\d+\.\d+)',
            re.MULTILINE
        )
        matches = pattern.findall(html)

        if not matches:
            return None, None

        # Extrair todas as coordenadas e calcular a média
        latitudes = [safe_val_dbl(lat) for lat, _ in matches if safe_val_dbl(lat) is not None]
        longitudes = [safe_val_dbl(lon) for _, lon in matches if safe_val_dbl(lon) is not None]

        if not latitudes or not longitudes:
            return None, None

        lat_media = sum(latitudes) / len(latitudes)
        lon_media = sum(longitudes) / len(longitudes)
        return lat_media, lon_media

    except Exception as e:
        return None, None

def calculate_distance(lat_orig, lon_orig, lat_dest, lon_dest, api_key):
    """
    Calcula a distância e tempo entre dois pontos usando a API TomTom
    Retorna: (distância em km, tempo em minutos)
    """
    if None in (lat_orig, lon_orig, lat_dest, lon_dest):
        return None, None
        
    api_url = f'https://api.tomtom.com/routing/1/calculateRoute/{lat_orig},{lon_orig}:{lat_dest},{lon_dest}/json?key={api_key}&maxAlternatives=5&sectionType=traffic&sectionType=urban&extendedRouteRepresentation=travelTime&travelMode=truck'
    
    try:
        response = requests.get(api_url)
        if response.status_code == 200:
            data = response.json()
            if "routes" in data and len(data["routes"]) > 0:
                summary = data["routes"][0]["summary"]
                # Converter metros para quilômetros
                distance_km = summary["lengthInMeters"] / 1000
                # Converter segundos para minutos
                time_minutes = summary["travelTimeInSeconds"] / 60
                return round(distance_km, 2), round(time_minutes, 2)
        return None, None
    except Exception:
        return None, None
# =====================================================
# 🔹 Interface Streamlit
# =====================================================
st.title('Cálculo entre Distâncias')

# Carregar o ficheiro excel
files = st.file_uploader("Upload de arquivo Excel", type=["xlsx"])

# leitura das folhas do excel
if files:
   try:
        # Ler para um DataFrame (corrige uso incorreto de Workbook)
        df = pd.read_excel(files, engine="openpyxl")

        # Verificação de colunas obrigatórias (ajustado para os nomes usados mais abaixo)
        required_cols = ["CP_Partida",  "CPP_1",  "CPP_2", "CP_Chegada",  "CPC_1",  "CPC_2", "Dist_Result", "Tempo_Result"]
        if not set(required_cols).issubset(df.columns):
            st.error(f"O ficheiro deve conter as colunas: {', '.join(required_cols)}")
        else:
            st.info("A processar coordenadas... isto pode demorar alguns minutos ⏳")

            results = []
            logs = []
            progress = st.progress(0.0)
            total = len(df)

            api_key = 'c3XHbxJPleK7qYyIzs9moDgxxu5sjRRW'
        
            for idx, row in df.iterrows():
                cp4_p, cp3_p = row.get("CPP_1"), row.get("CPP_2")
                cp4_c, cp3_c = row.get("CPC_1"), row.get("CPC_2")

                lat_p, lon_p = get_coordinates(cp4_p, cp3_p)
                lat_c, lon_c = get_coordinates(cp4_c, cp3_c)
                
                # Calcular distância e tempo usando TomTom API
                distance, time = calculate_distance(lat_p, lon_p, lat_c, lon_c, api_key)

                results.append({
                    "CP_Partida": f"{str(cp4_p).zfill(4)}-{str(cp3_p).zfill(3)}",
                    "Latitude_Partida": lat_p,
                    "Longitude_Partida": lon_p,
                    "CP_Chegada": f"{str(cp4_c).zfill(4)}-{str(cp3_c).zfill(3)}",
                    "Latitude_Chegada": lat_c,
                    "Longitude_Chegada": lon_c,
                    "Dist_Result": distance,  
                    "Tempo_Result": time      
                })

                if lat_p is None or lon_p is None:
                    logs.append(f"Sem coordenadas para {cp4_p}-{cp3_p}")
                if lat_c is None or lon_c is None:
                    logs.append(f"Sem coordenadas para {cp4_c}-{cp3_c}")

                if total > 0:
                    progress.progress((idx + 1) / total)

            # Criar DataFrame de resultados
            result_df = pd.DataFrame(results)
            
            valid_distances = result_df["Dist_Result"].dropna()
            if not valid_distances.empty:
                st.write("📊 Estatísticas das distâncias (km):")
                st.write(f"Média: {valid_distances.mean():.2f} km")
                          
            valid_time = result_df["Tempo_Result"].dropna()
            if not valid_time.empty:
                st.write("⏱️ Estatísticas do tempo de viagem (minutos):")
                st.write(f"Média: {valid_time.mean():.2f} min")

            st.success("Processamento concluído ✅")
            st.dataframe(result_df)

            # Mostrar logs se existirem
            if logs:
                st.warning("Alguns códigos não tiveram coordenadas encontradas:")
                for log in logs:
                    st.text(log)

            # Preparar ficheiro Excel para download (usar pd.ExcelWriter)
            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                result_df.to_excel(writer, index=False, sheet_name="Coordenadas")
            buffer.seek(0)

            st.download_button(
                label="📥 Descarregar resultados em Excel",
                data=buffer.getvalue(),
                file_name="coordenadas_cp.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

   except Exception as e:
        st.error(f"Ocorreu um erro ao ler o ficheiro: {e}")