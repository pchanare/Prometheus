import os
import subprocess
import requests
from dotenv import load_dotenv

load_dotenv()

# 1. Get the token
try:
    token = subprocess.check_output("gcloud auth print-access-token", shell=True).decode("utf-8").strip()
except:
    print("❌ Error: Could not get token. Run 'gcloud auth login' first.")
    exit()

# 2. Set the Project ID manually for this test
PROJECT_ID = "prometheus-489421" 

# 3. Build the request with the "Quota Project" header
headers = {
    "Authorization": f"Bearer {token}",
    "X-Goog-User-Project": PROJECT_ID  # <--- THIS IS THE KEY FIX
}

url = "https://solar.googleapis.com/v1/buildingInsights:findClosest?location.latitude=37.4450&location.longitude=-122.1390"

print(f"Testing Solar API for project: {PROJECT_ID}...")

response = requests.get(url, headers=headers)

if response.status_code == 200:
    print("✅ SUCCESS! The Solar API is responding.")
    print(f"Result: {response.json().get('name')}")
else:
    print(f"❌ Still failing. Error {response.status_code}: {response.text}")