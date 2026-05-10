import requests
import json

url = "http://n8n.estrellitastudio.es:8091/mcp-server/http"
token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhYjRiNzM0YS1lYmU3LTRkMTYtOTVkOC1mNDk1ZDlkODAxMzEiLCJpc3MiOiJuOG4iLCJhdWQiOiJtY3Atc2VydmVyLWFwaSIsImp0aSI6ImY5M2RkM2YxLTA5NWUtNDkyMS04OGQzLTJmZmU1ODhjOWY5YyIsImlhdCI6MTc3NTcxMjQ2N30.zUg-nB08h_VfomJ4w8ASjwX2vr4dJWx5Yj-Ga0syn3s"

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {token}"
}

payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list"
}

try:
    response = requests.post(url, headers=headers, json=payload, timeout=10)
    print(f"Status Code: {response.status_code}")
    print("Response JSON:")
    print(json.dumps(response.json(), indent=2))
except Exception as e:
    print(f"Error: {e}")
