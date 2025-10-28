from urllib import response
import streamlit as st
import requests

st.title('Cálculo Coordenadas')

# API Key e URL base
api_key = 'c3XHbxJPleK7qYyIzs9moDgxxu5sjRRW'
api_url = 'https://api.tomtom.com/routing/1/calculateRoute/{latOrig},{lonOrig}:{latDest},{lonDest}/json?key={api_key}&travelMode=truck'

# Inputs
latOrig = st.text_input("Latitude Origem")
lonOrig = st.text_input("Longitude Origem")
latDest = st.text_input("Latitude Destino")
lonDest = st.text_input("Longitude Destino")

# Botão para calcular
botão = st.button("Calcular Rota")

# Função para buscar respostas
def fetch_rout(api_url):
    response = requests.get(api_url)
    if response.status_code == 200:
        return response.json()
    else:
        return f"Erro ao obter respostas: {response.status_code}"

# Quando o botão é clicado
if botão:
    # Verifica se todos os campos estão preenchidos
    if latOrig and lonOrig and latDest and lonDest:
        # Substitui os placeholders na URL com os valores dos inputs
        url = api_url.format(latOrig=latOrig, lonOrig=lonOrig, latDest=latDest, lonDest=lonDest, api_key=api_key)
        
        # Faz a chamada à API
        result = fetch_rout(url)
        
        # Mostra o resultado
        if isinstance(result, dict):
            # Exibe o JSON completo
            st.json(result)
            
            # Exibe informações específicas da rota
            if "routes" in result and len(result["routes"]) > 0:
                route_summary = result["routes"][0]["summary"]
                st.write("Resumo da Rota:")
                st.write(f"Distância: {route_summary['lengthInMeters']} metros")
                st.write(f"Tempo de viagem: {route_summary['travelTimeInSeconds']} segundos")
                st.write(f"Atraso no trânsito: {route_summary['trafficDelayInSeconds']} segundos")
                st.write(f"Hora de partida: {route_summary['departureTime']}")
                st.write(f"Hora de chegada: {route_summary['arrivalTime']}")
        else:
            st.error(result)
    else:
        st.warning("Por favor, preencha todos os campos.")
