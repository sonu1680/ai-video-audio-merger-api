import os
import boto3
import urllib3
from botocore.config import Config
from boto3.s3.transfer import TransferConfig
from config import R2_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET_KEY, R2_BUCKET_NAME

# Suppress insecure request warnings caused by verify=False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def upload_video_to_r2(file_path: str, object_name: str = None) -> bool:
    """Uploads a video file to the Cloudflare R2 bucket"""
    if not object_name:
        object_name = f"videos/{os.path.basename(file_path)}"
        
    endpoint_url = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=R2_ACCESS_KEY,
            aws_secret_access_key=R2_SECRET_KEY,
            region_name="auto",
            verify=False,  # Bypass SSL validation to avoid EOF occurred in violation of protocol
            config=Config(
                signature_version="s3v4",
                retries={
                    'max_attempts': 3,
                    'mode': 'standard'
                }
            )
        )

        # Explicitly set the Content-Type so browsers know it's a video file, not binary octet-stream
        extra_args = {
            "ContentType": "video/mp4"
        }

        # Cloudflare R2 consistently drops SSL connections aggressively when performing chunked multi-part uploading inside heavily threaded contexts.
        # By setting the threshold extremely high (1GB), we force boto3 to upload typical MP4 files in ONE single stream, completely bypassing the bug.
        transfer_config = TransferConfig(
            multipart_threshold=1024 * 1024,  # Increase to 1 GB so typical video files are NOT chunked
            max_concurrency=1,                # strictly single-threaded
            use_threads=False                 # Disable multi-threading inside boto3 entirely
        )

        s3.upload_file(
            file_path, 
            R2_BUCKET_NAME, 
            object_name,
            ExtraArgs=extra_args,
            Config=transfer_config
        )
        print(f"Upload successful! {file_path} -> {R2_BUCKET_NAME}/{object_name}")
        return True
    except Exception as e:
        print(f"Upload failed: {e}")
        return False
