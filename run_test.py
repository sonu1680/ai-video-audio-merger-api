import json
import logging
import sys
from pathlib import Path
from config import VIDEOS_DIR

# Ensure videos directory exists
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

import logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("TestRun")

from modules.video_processor import generate_modules_sequentially

def run():
    print("Testing payload generation...")
    with open('test_payload.json', 'r') as f:
        payload = json.load(f)

    story_id = payload['stories'][0]['id']
    modules = payload['stories'][0]['modules']

    try:
        videos = generate_modules_sequentially(str(story_id), modules)
        print("ALL PASSED: ", videos)
    except Exception as e:
        print("PIPELINE FAILED: ", e)
        sys.exit(1)

if __name__ == "__main__":
    run()
