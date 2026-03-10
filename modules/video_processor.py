import asyncio
import logging
import json
import subprocess
from pathlib import Path
from typing import List
from playwright.sync_api import sync_playwright
import app as grok_app
from config import IMAGE_PATH, VIDEOS_DIR

log = logging.getLogger("GrokAPI.VideoProcessor")

def extract_last_frame(video_path: str, output_image_path: str) -> bool:
    """Uses ffmpeg to extract the last frame of a video."""
    log.info(f"🎞️ Extracting last frame from: {video_path}")
    try:
        # -sseof -3 goes to the end of the file, we just grab a frame from the end
        cmd = [
            'ffmpeg',
            '-y',  # overwrite
            '-sseof', '-0.1', # near end
            '-i', video_path,
            '-vframes', '1',
            '-q:v', '2', # high quality
            output_image_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0 and Path(output_image_path).exists():
            log.info(f"✅ Last frame extracted to: {output_image_path}")
            return True
        else:
            log.error(f"❌ Failed to extract last frame. stderr: {result.stderr}")
            return False
    except Exception as e:
        log.error(f"❌ Exception extracting frame: {e}")
        return False

def generate_modules_sequentially(story_id: str, modules: List[dict]) -> List[Path]:
    """
    Generates mp4 videos for each module sequentially using Playwright.
    Returns the paths to the generated modules.
    """
    log.info(f"[story_id: {story_id}] 🚀 Starting browser session for sequential processing")
    
    generated_videos = []
    
    try:
        p = sync_playwright().start()
        # We start session WITHOUT an image initially, as we will generate or upload it dynamically
        session = grok_app.start_session(None, p)
        session["p_context"] = p
    except Exception as e:
        log.error(f"[story_id: {story_id}] ❌ Failed to start browser session: {e}")
        raise RuntimeError(f"Playwright init failed: {e}")
    
    if session.get("status") != "success":
        log.error(f"[story_id: {story_id}] ❌ Failed to start session: {session.get('error')}")
        raise RuntimeError(f"Session init failed: {session.get('error')}")

    browser = session["browser"]
    page = session["page"]
    session_log = session["log"]
    p_context = session["p_context"]

    try:
        current_image_path = None

        for idx, module in enumerate(modules):
            module_number = module.get("module_number")
            video_prompt = module.get("video_generation_prompt")
            image_prompt = module.get("image_generation_prompt", "")
            
            if not isinstance(video_prompt, str):
                video_prompt_str = json.dumps(video_prompt, ensure_ascii=False, indent=2)
            else:
                video_prompt_str = video_prompt
                
            output_video_filename = f"module_{module_number}.mp4"
            output_video_path = str(VIDEOS_DIR / output_video_filename)
            
            # --- Resume / Backup Logic ---
            if Path(output_video_path).exists() and Path(output_video_path).stat().st_size > 0:
                log.info(f"[story_id: {story_id}] [module_number: {module_number}] ⏭️ Video already exists. Skipping generation.")
                generated_videos.append(Path(output_video_path))
                continue
            # -----------------------------
            
            if idx == 0:
                # Module 1 Logic: Send both prompts combined
                final_video_prompt = f"IMAGE CONTEXT:\n{image_prompt}\n\nVIDEO INSTRUCTIONS:\n{video_prompt_str}".strip()
                grok_app._stage_video_mode(page, session_log)
                log.info(f"[story_id: {story_id}] [module_number: {module_number}] 🎬 Generating initial video using text prompt")
            else:
                final_video_prompt = video_prompt_str
                # Module N Logic: Extract last frame from N-1
                prev_video_path = str(generated_videos[-1])
                extract_filename = f"module_{module_number}_start_frame.jpg"
                current_image_path = str(VIDEOS_DIR / extract_filename)
                
                ext_res = extract_last_frame(prev_video_path, current_image_path)
                if not ext_res:
                    raise RuntimeError(f"Failed to extract frame from previous video: {prev_video_path}")

                grok_app._stage_video_mode(page, session_log)
                grok_app._stage_upload_image(page, current_image_path, session_log)

            # Generate the video
            success = False
            retries = 0
            max_retries = 3
            
            while retries <= max_retries and not success:
                log.info(f"[story_id: {story_id}] [module_number: {module_number}] 🎬 prompt submitted (Attempt {retries + 1})")
                
                try:
                    # generate_single_video is synchronous
                    result = grok_app.generate_single_video(page, final_video_prompt, output_video_path, session_log)
                    
                    if result.get("status") == "success":
                        log.info(f"[story_id: {story_id}] [module_number: {module_number}] ✅ video generated (file: {result['file_path']})")
                        success = True
                        generated_videos.append(Path(result['file_path']))
                    else:
                        raise RuntimeError(result.get("error"))
                        
                except Exception as e:
                    log.error(f"[story_id: {story_id}] [module_number: {module_number}] ❌ failure: {e}")
                    if retries < max_retries:
                        log.info(f"[story_id: {story_id}] [module_number: {module_number}] 🔄 retry attempt {retries + 1}")
                        import time
                        time.sleep(5)
                    retries += 1
                    
            if not success:
                log.error(f"[story_id: {story_id}] 🛑 stopped processing story due to module {module_number} failure")
                raise RuntimeError(f"Failed to generate module {module_number} after {max_retries} retries")

        return generated_videos
    finally:
        grok_app.close_session(browser, session_log)
        if p_context:
            p_context.stop()
        log.info(f"[story_id: {story_id}] 👋 Browser session closed")
