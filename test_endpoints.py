import requests
import json
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

api_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhYjRiNzM0YS1lYmU3LTRkMTYtOTVkOC1mNDk1ZDlkODAxMzEiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwianRpIjoiNGIwMzdkNzctNjAzMy00N2ZlLWIxNzEtYjViMjVjNzRjZmY4IiwiaWF0IjoxNzc1NzEyOTMyfQ.DPCpdRdCZIwvARwJE4B_hQUlAHvTIU7P2tbbGwxtQko"

headers = {
    "X-N8N-API-KEY": api_key,
    "Accept": "application/json"
}

endpoints = [
    "https://n8n.estrellitastudio.es/api/v1/workflows",
    "http://n8n.estrellitastudio.es/api/v1/workflows",
    "http://127.0.0.1:8091/api/v1/workflows",
    "http://127.0.0.1:5678/api/v1/workflows",
    "http://localhost:8091/api/v1/workflows"
]

print("Testing alternative endpoints...")
for url in endpoints:
    print(f"\nTrying: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=5, verify=False)
        print(f"Status Code: {response.status_code}")
        if response.status_code in [200, 401, 403]:
            print(f">>> SUCCESS! Reachable endpoint: {url}")
            if response.status_code == 200:
                print("Authentication successful!")
            break
    except Exception as e:
        print(f"Failed: {type(e).__name__} - {e}")
