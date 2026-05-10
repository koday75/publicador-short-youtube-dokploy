import requests
import json
import time

# Configuración local
ENGINE_URL = "http://localhost:8000"

def test_engine_niche():
    print("Iniciando prueba de motor con nicho...")
    
    payload = {
        "text": "Esta es una prueba de renderizado para el nicho de curiosidades. El sistema debe buscar el vídeo en la carpeta correspondiente.",
        "background_video_name": "default.mp4",
        "niche": "curiosidades",
        "voice_id": "pNInz6obpgnuM07pZNoR"
    }
    
    try:
        response = requests.post(f"{ENGINE_URL}/render", json=payload)
        
        if response.status_code == 200:
            data = response.json()
            print("\n[ÉXITO] Vídeo solicitado correctamente.")
            print(f"ID del trabajo: {data.get('job_id')}")
            print(f"URL de descarga: {data.get('video_url')}")
            print(f"Ruta local: {data.get('local_path')}")
        else:
            print(f"\n[ERROR] El motor respondió con estado: {response.status_code}")
            print(f"Detalle: {response.text}")
            
    except Exception as e:
        print(f"\n[ERROR] No se pudo conectar con el motor: {e}")
        print("Asegúrate de que el motor esté corriendo (python main.py en shorts-engine)")

if __name__ == "__main__":
    test_engine_niche()
