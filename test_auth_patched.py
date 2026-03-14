import time
from google.oauth2 import service_account
from google.auth.transport.requests import Request
import traceback

# Backdate the clock by exactly 1 year and 1 week roughly, just in case 2026 is wrong
original_time = time.time

def fake_time():
    # current time is around 1773174621 (March 2026)
    # returning a hardcoded timestamp for sometime in 2025, or just subtract 365 days
    return original_time() - (365 * 24 * 3600)

time.time = fake_time

try:
    creds = service_account.Credentials.from_service_account_file(
        "ai-video-generator-9f07b-e197aeaa0b1c.json",
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(Request())
    print("SUCCESS: Token retrieved with modified time!")
except Exception as e:
    print(f"FAILED: {e}")
