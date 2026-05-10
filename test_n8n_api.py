import requests
import json

url = "http://n8n.estrellitastudio.es:8091/api/v1/workflows"
api_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhYjRiNzM0YS1lYmU3LTRkMTYtOTVkOC1mNDk1ZDlkODAxMzEiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwianRpIjoiNGIwMzdkNzctNjAzMy00N2ZlLWIxNzEtYjViMjVjNzRjZmY4IiwiaWF0IjoxNzc1NzEyOTMyfQ.DPCpdRdCZIwvARwJE4B_hQUlAHvTIU7P2tbbGwxtQko"

headers = {
    "X-N8N-API-KEY": api_key,
    "Accept": "application/json"
}

try:
    response = requests.get(url, headers=headers, timeout=10)
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        print("Successfully connected to n8n Public API!")
        print("Workflows:")
        print(json.dumps(response.json(), indent=2))
    else:
        print(f"Failed to connect. Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")
