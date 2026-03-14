import requests
import json

with open("test_payload.json", "r") as f:
    payload = json.load(f)

response = requests.post("http://localhost:8000/api/process_payload", json=payload)
print(response.json())
