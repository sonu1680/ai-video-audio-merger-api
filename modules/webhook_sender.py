import os
import requests
import logging
from config import N8N_WEBHOOK_URL, VIDEO_PUBLIC_DOMAIN

log = logging.getLogger("GrokAPI.Webhook")

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
        
        # If the local video path is provided, attach it as a file
        if source_video_path and os.path.exists(source_video_path):
            with open(source_video_path, "rb") as video_file:
                # The 'file' key matches what many webhook consumers expect for the binary payload
                files = {
                    "file": (os.path.basename(source_video_path), video_file, "video/mp4")
                }
                response = requests.post(N8N_WEBHOOK_URL, data=data_payload, files=files, timeout=120)
        else:
            log.warning(f"[story_id: {story_id}] ⚠️ Source video path missing or invalid, sending JSON webhook only.")
            response = requests.post(N8N_WEBHOOK_URL, json=data_payload, timeout=10)
        
        if response.status_code in (200, 201, 202):
            log.info(f"[story_id: {story_id}] ✅ Webhook sent successfully")
            return True
        else:
            log.warning(f"[story_id: {story_id}] ⚠️ Webhook returned status {response.status_code}: {response.text}")
            return False
            
    except Exception as e:
        log.error(f"[story_id: {story_id}] ❌ Webhook notification failed: {e}")
        return False
