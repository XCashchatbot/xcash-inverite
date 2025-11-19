import requests

data = {
    "first_name": "latchman",
    "last_name": "shivprashad",
    "loan_type": "payday",
    "loan_amount": "500",
    "address": "1203 westmount Ave Innisfil, Ontario L9s4z7"



}

# ✅ Use the correct endpoint
response = requests.post("http://127.0.0.1:5000/webhook/payday", json=data)

print("Status Code:", response.status_code)
print("Raw Response:", response.text)

try:
    print("Parsed JSON:", response.json())
except Exception as e:
    print("❌ Failed to parse JSON:", str(e))

