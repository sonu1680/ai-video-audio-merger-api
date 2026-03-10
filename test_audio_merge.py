import logging
from pathlib import Path
from modules.video_merger import merge_videos
from modules.voiceover import generate_speech

logging.basicConfig(level=logging.INFO)

def run():
    print("Testing merge...")
    
    # 1. Create a dummy voiceover
    vo_path = Path("videos/voiceover_test99.wav")
    if not vo_path.exists():
        generate_speech("नमस्कार! यह एक परीक्षण है।", output_filename=str(vo_path))
        
    print(f"Voiceover created at {vo_path}")
    
    # We'll re-use the successful module_1 from earlier
    videos = [Path("videos/module_1.mp4")]
    if not videos[0].exists():
        print("Missing module 1 test file!")
        return

    # 2. Test Merge
    try:
        final_video = merge_videos("test99", videos, voiceover_path=vo_path)
        print("SUCCESS! Final video located at: ", final_video)
    except Exception as e:
        print("FAIL: ", e)

if __name__ == "__main__":
    run()
