import os
import boto3
from botocore.config import Config
from boto3.s3.transfer import TransferConfig
from config import R2_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET_KEY, R2_BUCKET_NAME

def upload_video_to_r2(file_path: str, object_name: str = None) -> bool:
    """Uploads a video file to Cloudflare R2 bucket safely"""
    
    if not object_name:
        object_name = f"videos/{os.path.basename(file_path)}"
    
    endpoint_url = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

    try:
        # Configure boto3 client
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=R2_ACCESS_KEY,
            aws_secret_access_key=R2_SECRET_KEY,
            region_name="auto",
            verify=True,  # Enable SSL verification (recommended)
            config=Config(
                signature_version="s3v4",
                retries={'max_attempts': 10, 'mode': 'standard'}
            )
        )

        # Extra arguments to set content type
        extra_args = {"ContentType": "video/mp4", "ACL": "public-read"}

        # Force single-stream upload to avoid R2 SSL drops
        transfer_config = TransferConfig(
            multipart_threshold=1024*1024*1024,  # 1 GB, so most videos are single-stream
            max_concurrency=1,
            use_threads=False
        )

        s3.upload_file(
            file_path,
            R2_BUCKET_NAME,
            object_name,
            ExtraArgs=extra_args,
            Config=transfer_config
        )

        print(f"Upload successful: {file_path} -> {R2_BUCKET_NAME}/{object_name}")
        return True

    except boto3.exceptions.S3UploadFailedError as s3_err:
        print(f"S3 Upload Failed: {s3_err}")
        return False
    except Exception as e:
        print(f"Upload failed: {e}")
        return False