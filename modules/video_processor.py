import asyncio
import logging
import json
from pathlib import Path
from typing import List
from playwright.sync_api import sync_playwright
import app as grok_app
from config import IMAGE_PATH, VIDEOS_DIR

log = logging.getLogger("GrokAPI.VideoProcessor")

def generate_modules_sequentially(story_id: str, modules: List[dict]) -> List[Path]:
    """
    Generates mp4 videos for each module sequentially using Playwright.
    Returns the paths to the generated modules.
    """
    log.info(f"[story_id: {story_id}] 🚀 Starting browser session for sequential processing")
    
    generated_videos = []
    
    try:
        p = sync_playwright().start()
        session = grok_app.start_session(IMAGE_PATH, p)
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
        for module in modules:
            # We assume dict because BaseModel was passed in from API layer
            module_number = module.get("module_number")
            prompt = module.get("video_generation_prompt")
            
            if not isinstance(prompt, str):
                prompt = json.dumps(prompt, ensure_ascii=False)
                
            output_filename = f"module_{module_number}.mp4"
            output_path = str(VIDEOS_DIR / output_filename)
            
            success = False
            retries = 0
            max_retries = 3
            
            while retries <= max_retries and not success:
                log.info(f"[story_id: {story_id}] [module_number: {module_number}] 🎬 prompt submitted (Attempt {retries + 1})")
                
                try:
                    # generate_single_video is synchronous
                    result = grok_app.generate_single_video(page, prompt, output_path, session_log)
                    
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
                        time.sleep(5) # synchronous sleep since this runs in executor thread
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
