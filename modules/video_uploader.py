import os
import boto3
from config import R2_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET_KEY, R2_BUCKET_NAME

def upload_video_to_r2(file_path: str, object_name: str = None) -> bool:
    """Uploads a video file to the Cloudflare R2 bucket"""
    if not object_name:
        object_name = f"videos/{os.path.basename(file_path)}"
        
    endpoint_url = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

    try:
        s3 = boto3.client(
            "s3",
            endpoint_url = endpoint_url,
            aws_access_key_id = R2_ACCESS_KEY,
            aws_secret_access_key = R2_SECRET_KEY,
        )

        # Explicitly set the Content-Type so browsers know it's a video file, not binary octet-stream
        extra_args = {
            "ContentType": "video/mp4"
        }

        s3.upload_file(
            file_path, 
            R2_BUCKET_NAME, 
            object_name,
            ExtraArgs=extra_args
        )
        print(f"Upload successful! {file_path} -> {R2_BUCKET_NAME}/{object_name}")
        return True
    except Exception as e:
        print(f"Upload failed: {e}")
        return False
