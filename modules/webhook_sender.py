import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
import logging
from config import N8N_WEBHOOK_URL, VIDEO_PUBLIC_DOMAIN

# Suppress insecure request warnings if verify=False is used
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger("GrokAPI.Webhook")

def create_retrying_session() -> requests.Session:
    session = requests.Session()
    # n8n can be slow and easily overloaded, especially on self-hosted instances on long multipart uploads.
    # Give it breathing room with higher backoffs.
    retry = Retry(
        total=5,
        read=5,      # Timeouts during the reading of a response
        connect=5,   # Timeouts during the connection phase
        backoff_factor=2.0, # Wait 2s, 4s, 8s, 16s between retries
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=None  # Retry on all methods including POST
    )
    
    # Configure the adapter with a larger pool block size to prevent ConnectionPool exhaustion
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=10,
        pool_maxsize=10,
        pool_block=True
    )
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def send_n8n_webhook(story_id: str, bucket_filename: str, timestamp_str: str, source_video_path: str = None) -> bool:
    """Sends a successful video generation payload and the MP4 file to the n8n webhook."""
    try:
        public_video_url = f"{VIDEO_PUBLIC_DOMAIN}/{bucket_filename}"
        
        # Text fields
        data_payload = {
            "story_id": story_id,
            "video_url": public_video_url,
            "timestamp": timestamp_str
        }
        
        log.info(f"[story_id: {story_id}] 📡 Sending webhook to {N8N_WEBHOOK_URL} with multipart/form-data")
        
        session = create_retrying_session()
        
        # If the local video path is provided, attach it as a file
        if source_video_path and os.path.exists(source_video_path):
            with open(source_video_path, "rb") as video_file:
                # The 'file' key matches what many webhook consumers expect for the binary payload
                files = {
                    "file": (os.path.basename(source_video_path), video_file, "video/mp4")
                }
                
                # We need a long timeout for large payloads. N8n can sometimes be very slow to process multipart forms.
                # Increase timeout to 300 seconds (5 minutes) and also prevent SSL truncation bugs.
                response = session.post(
                    N8N_WEBHOOK_URL, 
                    data=data_payload, 
                    files=files, 
                    timeout=300, 
                    verify=False,
                    stream=False # Try to push it as a single block to avoid read timeout drops
                )
        else:
            log.warning(f"[story_id: {story_id}] ⚠️ Source video path missing or invalid, sending JSON webhook only.")
            response = session.post(N8N_WEBHOOK_URL, json=data_payload, timeout=10, verify=False)
        
        if response.status_code in (200, 201, 202):
            log.info(f"[story_id: {story_id}] ✅ Webhook sent successfully")
            return True
        else:
            log.warning(f"[story_id: {story_id}] ⚠️ Webhook returned status {response.status_code}: {response.text}")
            return False
            
    except Exception as e:
        log.error(f"[story_id: {story_id}] ❌ Webhook notification failed: {e}")
        return False
