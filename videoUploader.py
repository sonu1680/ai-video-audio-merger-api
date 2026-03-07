import boto3

ACCESS_KEY = "233810c82d9efd1375a5c9151bd88468"
SECRET_KEY = "05e61949446f435cc659e88dafb01abbe8cf5597a91a18f9909b6a79dfb71452"
ACCOUNT_ID = "6613a5931848e80d555ccf73b6e553e0"

BUCKET_NAME = "ai-videos"

def upload_video_to_r2(file_path: str, object_name: str = None) -> bool:
    import os
    if not object_name:
        object_name = f"videos/{os.path.basename(file_path)}"
        
    endpoint_url = f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com"

    try:
        s3 = boto3.client(
            "s3",
            endpoint_url = endpoint_url,
            aws_access_key_id = ACCESS_KEY,
            aws_secret_access_key = SECRET_KEY,
        )

        s3.upload_file(file_path, BUCKET_NAME, object_name)
        print(f"Upload successful! {file_path} -> {BUCKET_NAME}/{object_name}")
        return True
    except Exception as e:
        print(f"Upload failed: {e}")
        return False