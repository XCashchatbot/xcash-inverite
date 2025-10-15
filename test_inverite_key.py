import requests
import os
from dotenv import load_dotenv

load_dotenv()
INVERITE_API_KEY = os.getenv("INVERITE_API_KEY")

# Example existing endpoint: use your real GUID here if available
guid = "F19FCD21-8098-4EEC-84F9-062FC7A292F5"
url = f"https://www.inverite.com/api/v2/fetch/{guid}"
headers = {
    "Auth": INVERITE_API_KEY.strip(),
    "Accept": "application/json"
}

print(f"🔍 Testing Inverite API key: {INVERITE_API_KEY[:6]}********")
response = requests.get(url, headers=headers, timeout=30)

print(f"Status Code: {response.status_code}")
print("Response:")
print(response.text[:500])  # print first 500 chars only

