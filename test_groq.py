import sqlite3
import requests

def test_groq():
    db = sqlite3.connect('storage/jobs.db')
    cursor = db.execute("SELECT key_value FROM settings WHERE key_name='GROQ_API_KEY'")
    row = cursor.fetchone()
    if not row:
        print("No GROQ_API_KEY found")
        return
    api_key = row[0].strip()

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    
    data = {
        "model": "llama3-70b-8192",
        "messages": [
            {"role": "system", "content": "You are a test assistant."},
            {"role": "user", "content": "Hello!"}
        ]
    }
    print(f"Sending request to {url} with model {data['model']}")
    res = requests.post(url, json=data, headers=headers)
    print(f"Status Code: {res.status_code}")
    print(f"Response: {res.text}")

if __name__ == "__main__":
    test_groq()
