from google.oauth2 import service_account
from google.auth.transport.requests import Request
import traceback

try:
    creds = service_account.Credentials.from_service_account_file(
        "ai-video-generator-9f07b-e197aeaa0b1c.json",
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(Request())
    print("SUCCESS: Token retrieved.")
except Exception as e:
    print(f"FAILED: {e}")
    traceback.print_exc()
