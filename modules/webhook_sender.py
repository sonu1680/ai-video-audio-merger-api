import requests
import logging
from config import N8N_WEBHOOK_URL, VIDEO_PUBLIC_DOMAIN

log = logging.getLogger("GrokAPI.Webhook")

def send_n8n_webhook(story_id: str, bucket_filename: str, timestamp_str: str) -> bool:
    """Sends a successful video generation payload to the n8n webhook."""
    try:
        public_video_url = f"{VIDEO_PUBLIC_DOMAIN}/{bucket_filename}"
        
        payload = {
            "story_id": story_id,
            "video_url": public_video_url,
            "timestamp": timestamp_str
        }
        log.info(f"[story_id: {story_id}] 📡 Sending webhook to {N8N_WEBHOOK_URL} with payload {payload}")
        
        response = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=10)
        
        if response.status_code in (200, 201, 202):
            log.info(f"[story_id: {story_id}] ✅ Webhook sent successfully")
            return True
        else:
            log.warning(f"[story_id: {story_id}] ⚠️ Webhook returned status {response.status_code}: {response.text}")
            return False
            
    except Exception as e:
        log.error(f"[story_id: {story_id}] ❌ Webhook notification failed: {e}")
        return False
