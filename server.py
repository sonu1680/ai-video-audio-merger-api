"""
Grok Video Generation – FastAPI Server
========================================
Endpoints:
  GET  /api/getvideo/{prompt}          – Generate & stream back the MP4
  POST /api/getvideo  body: {"prompt"} – Same but accepts JSON body
  GET  /health                         – Health check + queue status

Usage:
  uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import os
import sys
import uuid
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# Import our automation core
import app as grok_app

# ─────────────────────────── CONFIG ───────────────────────────────────────────

BASE_DIR     = Path(__file__).parent
VIDEOS_DIR   = BASE_DIR / "videos"          # temp storage for generated videos
MAX_QUEUE    = 5                             # max jobs waiting in queue

VIDEOS_DIR.mkdir(exist_ok=True)

# ─────────────────────────── LOGGING ──────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("GrokAPI")

# ─────────────────────────── QUEUE / SEMAPHORE ────────────────────────────────
# Only ONE Chrome session can run at a time; we serialize with a semaphore 
# and track active jobs for the /health endpoint.

_chrome_lock  = asyncio.Semaphore(1)   # serialise generation (1 at a time)
_pending_jobs: dict[str, dict] = {}    # job_id → {"status", "prompt", "path"}


# ─────────────────────────── LIFESPAN ─────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("🚀 GrokAPI server starting …")
    log.info(f"   Videos dir: {VIDEOS_DIR}")
    yield
    log.info("👋 GrokAPI server shutting down.")


# ─────────────────────────── APP ──────────────────────────────────────────────

app = FastAPI(
    title="Grok Video Generator API",
    description="POST or GET a text prompt → receive a generated MP4 video.",
    version="2.0.0",
    lifespan=lifespan,
)


# ─────────────────────────── SCHEMAS ──────────────────────────────────────────

class PromptBody(BaseModel):
    prompt: str

from typing import List, Any, Union, Optional
import json

class ModulePayload(BaseModel):
    module_number: int
    video_generation_prompt: Any

    class Config:
        extra = "allow"

class StoryPayload(BaseModel):
    id: Optional[Union[int, str]] = None
    story_id: Optional[Union[int, str]] = None
    modules: List[ModulePayload]

    class Config:
        extra = "allow"

class TestPayload(BaseModel):
    stories: List[StoryPayload]


# ─────────────────────────── HELPERS ──────────────────────────────────────────

async def _run_generation(prompt: str) -> str:
    """
    Run the blocking Playwright automation in a thread-pool executor
    while holding the Chrome semaphore so only one job runs at a time.
    """
    job_id     = uuid.uuid4().hex[:8]
    # Use default output path (output.mp4 in project root, replaces old one)
    output_path = str(BASE_DIR / "output.mp4")

    from app import IMAGE_PATH

    log.info(f"[{job_id}] Queued: «{prompt[:60]}»")
    _pending_jobs[job_id] = {"status": "queued", "prompt": prompt, "path": output_path}

    async with _chrome_lock:
        log.info(f"[{job_id}] Starting …")
        _pending_jobs[job_id]["status"] = "running"
        loop = asyncio.get_event_loop()
        try:
            # generate_video is synchronous – run in thread pool
            result = await loop.run_in_executor(None, grok_app.generate_video, prompt, IMAGE_PATH, output_path)
            
            if result["status"] == "success":
                _pending_jobs[job_id]["status"] = "done"
                log.info(f"[{job_id}] Done → {result['file_path']}")
                return result["file_path"]
            else:
                raise RuntimeError(result["error"])
        except Exception as e:
            _pending_jobs[job_id]["status"] = f"failed: {e}"
            log.error(f"[{job_id}] FAILED: {e}")
            raise RuntimeError(str(e)) from e


def _cleanup(path: str) -> None:
    """Delete temp video file after response is sent."""
    try:
        if os.path.exists(path):
            os.remove(path)
            log.info(f"🗑️  Cleaned up {path}")
    except Exception as e:
        log.warning(f"Cleanup failed for {path}: {e}")


async def _process_payload_sequentially(payload: Union[TestPayload, List[StoryPayload]]):
    stories = payload.stories if isinstance(payload, TestPayload) else payload
    
    from playwright.sync_api import sync_playwright
    from app import IMAGE_PATH, start_session, generate_single_video, close_session

    for story in stories:
        current_story_id = story.story_id if story.story_id is not None else story.id
        # Sort modules by module_number to ensure strict sequential processing
        modules = sorted(story.modules, key=lambda m: m.module_number)
        
        async with _chrome_lock:
            # We hold the lock for the entire story so the browser session is isolated.
            log.info(f"[story_id: {current_story_id}] 🚀 Starting browser session for sequential processing")
            
            loop = asyncio.get_event_loop()
            
            # Start Playwright & Session
            # Playwright must run in a thread, so we manage its context manually
            def _init_session():
                try:
                    p = sync_playwright().start()
                    session = start_session(IMAGE_PATH, p)
                    session["p_context"] = p
                    return session
                except Exception as e:
                    return {"status": "failure", "error": str(e)}

            session = await loop.run_in_executor(None, _init_session)
            
            if session.get("status") != "success":
                log.error(f"[story_id: {current_story_id}] ❌ Failed to start browser session: {session.get('error')}")
                continue

            browser = session["browser"]
            page = session["page"]
            session_log = session["log"]
            p_context = session["p_context"]

            try:
                for module in modules:
                    prompt = module.video_generation_prompt
                    if not isinstance(prompt, str):
                        import json
                        prompt = json.dumps(prompt, ensure_ascii=False)
                        
                    # Target filename logic: module_X.mp4
                    output_filename = f"module_{module.module_number}.mp4"
                    output_path = str(VIDEOS_DIR / output_filename)
                    
                    success = False
                    retries = 0
                    max_retries = 3
                    
                    while retries <= max_retries and not success:
                        log.info(f"[story_id: {current_story_id}] [module_number: {module.module_number}] 🎬 prompt submitted (Attempt {retries + 1})")
                        
                        try:
                            # Run the generation block
                            result = await loop.run_in_executor(
                                None, 
                                generate_single_video, 
                                page, prompt, output_path, session_log
                            )
                            
                            if result["status"] == "success":
                                log.info(f"[story_id: {current_story_id}] [module_number: {module.module_number}] ✅ video generated and downloaded (file: {result['file_path']})")
                                success = True
                            else:
                                raise RuntimeError(result["error"])
                                
                        except Exception as e:
                            log.error(f"[story_id: {current_story_id}] [module_number: {module.module_number}] ❌ failure: {e}")
                            if retries < max_retries:
                                log.info(f"[story_id: {current_story_id}] [module_number: {module.module_number}] 🔄 retry attempt {retries + 1}")
                                await asyncio.sleep(5)
                            retries += 1
                            
                    if not success:
                        log.error(f"[story_id: {current_story_id}] 🛑 stopped processing story due to module {module.module_number} failure after {max_retries} retries")
                        break
            finally:
                # Always close the session after finishing modules
                def _cleanup_session():
                    close_session(browser, session_log)
                    if p_context:
                        p_context.stop()
                await loop.run_in_executor(None, _cleanup_session)
                log.info(f"[story_id: {current_story_id}] 👋 Browser session closed")

                # Merge generated videos
                import subprocess
                concat_file = VIDEOS_DIR / f"concat_{current_story_id}.txt"
                
                bg_audio_path = BASE_DIR / "bg.mp3"
                has_bg_audio = bg_audio_path.exists()
                
                merged_output = VIDEOS_DIR / (f"temp_story_{current_story_id}_merged.mp4" if has_bg_audio else "finalmergevideo.mp4")
                
                try:
                    valid_videos = []
                    for module in modules:
                        output_path = VIDEOS_DIR / f"module_{module.module_number}.mp4"
                        if output_path.exists():
                            valid_videos.append(output_path)
                    
                    if valid_videos:
                        with open(concat_file, "w") as f:
                            for vp in valid_videos:
                                f.write(f"file '{vp.absolute()}'\n")
                                
                        log.info(f"[story_id: {current_story_id}] 🎬 Merging {len(valid_videos)} videos into {merged_output.name}")
                        cmd = [
                            "ffmpeg", "-y", "-f", "concat", "-safe", "0", 
                            "-i", str(concat_file.absolute()), 
                            "-c", "copy", str(merged_output.absolute())
                        ]
                        
                        def _run_ffmpeg():
                            return subprocess.run(cmd, capture_output=True, text=True)
                            
                        process = await loop.run_in_executor(None, _run_ffmpeg)
                        
                        if process.returncode == 0:
                            log.info(f"[story_id: {current_story_id}] ✅ Successfully merged videos to {merged_output}")
                            
                            final_video_path = merged_output
                            
                            # Add background audio if bg.mp3 exists
                            if has_bg_audio:
                                final_bg_output = VIDEOS_DIR / "finalmergevideo.mp4"
                                log.info(f"[story_id: {current_story_id}] 🎵 Adding background audio to {final_bg_output.name}")
                                bg_cmd = [
                                    "ffmpeg", "-i", str(merged_output.absolute()), 
                                    "-stream_loop", "-1", 
                                    "-i", str(bg_audio_path.absolute()), 
                                    "-filter_complex", "[1:a]volume=0.3[a1];[0:a][a1]amix=inputs=2:duration=first:dropout_transition=2[a]", 
                                    "-map", "0:v", "-map", "[a]", 
                                    "-c:v", "copy", "-y", str(final_bg_output.absolute())
                                ]
                                
                                def _run_ffmpeg_bg():
                                    return subprocess.run(bg_cmd, capture_output=True, text=True)
                                    
                                bg_process = await loop.run_in_executor(None, _run_ffmpeg_bg)
                                
                                if bg_process.returncode == 0:
                                    log.info(f"[story_id: {current_story_id}] ✅ Successfully added background audio to {final_bg_output}")
                                    final_video_path = final_bg_output
                                    try:
                                        merged_output.unlink()
                                    except:
                                        pass
                                else:
                                    log.error(f"[story_id: {current_story_id}] ❌ Failed to add background audio: {bg_process.stderr}")

                            # Upload the final video to R2 Bucket
                            try:
                                import datetime
                                from videoUploader import upload_video_to_r2
                                
                                # Setup a safe filename for the upload using current timestamp
                                timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                                bucket_filename = f"videos/video_{timestamp_str}.mp4"

                                log.info(f"[story_id: {current_story_id}] ☁️ Uploading {final_video_path.name} to R2 bucket as {bucket_filename}...")
                                
                                def _run_upload():
                                    return upload_video_to_r2(str(final_video_path.absolute()), bucket_filename)
                                    
                                upload_success = await loop.run_in_executor(None, _run_upload)
                                if upload_success:
                                    log.info(f"[story_id: {current_story_id}] ✅ Successfully uploaded {final_video_path.name} to R2 as {bucket_filename}")
                                    
                                    # Send webhook notification to n8n
                                    try:
                                        import requests
                                        webhook_url = "https://n8n.sonupandit.in/webhook-test/7fdf6dcd-d193-4dc2-96fc-1b9420446a21"
                                        
                                        # Construct public URL Assuming the bucket is mapped to sonupandit.in
                                        public_video_url = f"https://sonupandit.in/{bucket_filename}"
                                        
                                        payload = {
                                            "story_id": current_story_id,
                                            "video_url": public_video_url,
                                            "timestamp": timestamp_str
                                        }
                                        log.info(f"[story_id: {current_story_id}] 📡 Sending webhook to {webhook_url}")
                                        
                                        def _send_webhook():
                                            return requests.post(webhook_url, json=payload, timeout=10)
                                            
                                        webhook_resp = await loop.run_in_executor(None, _send_webhook)
                                        if webhook_resp.status_code in (200, 201, 202):
                                            log.info(f"[story_id: {current_story_id}] ✅ Webhook sent successfully")
                                        else:
                                            log.warning(f"[story_id: {current_story_id}] ⚠️ Webhook returned status {webhook_resp.status_code}: {webhook_resp.text}")
                                    except Exception as we:
                                        log.error(f"[story_id: {current_story_id}] ❌ Webhook notification failed: {we}")
                                        
                                else:
                                    log.error(f"[story_id: {current_story_id}] ❌ R2 upload failed")
                            except Exception as e:
                                log.error(f"[story_id: {current_story_id}] ❌ Exception during R2 upload: {e}")
                            
                        else:
                            log.error(f"[story_id: {current_story_id}] ❌ Failed to merge videos: {process.stderr}")
                    else:
                        log.warning(f"[story_id: {current_story_id}] ⚠️ No videos generated, skipping merge.")
                except Exception as e:
                    log.error(f"[story_id: {current_story_id}] ❌ Error during video merging: {e}")
                finally:
                    if concat_file.exists():
                        try:
                            concat_file.unlink()
                        except:
                            pass


# ─────────────────────────── ROUTES ───────────────────────────────────────────

@app.get("/health", summary="Health check + queue status")
async def health():
    return JSONResponse({
        "status": "ok",
        "active_jobs": len(_pending_jobs),
        "jobs": {
            jid: {"status": info["status"], "prompt": info["prompt"][:80]}
            for jid, info in _pending_jobs.items()
        },
    })


@app.get(
    "/api/getvideo/{prompt:path}",
    summary="Generate a video from a URL-encoded prompt",
    response_description="The generated MP4 video file",
)
async def get_video_from_path(prompt: str, background_tasks: BackgroundTasks):
    """
    **Example:**
    ```
    GET /api/getvideo/a dog playing in the snow
    ```
    The prompt can contain spaces and special chars (URL-encoded by the client).
    Returns the MP4 file directly in the response body.
    """
    return await _handle_generation(prompt, background_tasks)


@app.post(
    "/api/getvideo",
    summary="Generate a video from a JSON body prompt",
    response_description="The generated MP4 video file",
)
async def post_video(body: PromptBody, background_tasks: BackgroundTasks):
    """
    **Example:**
    ```json
    POST /api/getvideo
    {"prompt": "a dog playing in the snow"}
    ```
    """
    return await _handle_generation(body.prompt, background_tasks)


@app.post(
    "/api/process_payload",
    summary="Process a test payload sequentially",
)
async def process_test_payload(payload: Union[TestPayload, List[StoryPayload]], background_tasks: BackgroundTasks):
    """
    Process a payload of stories with sequential video generation modules.
    """
    background_tasks.add_task(_process_payload_sequentially, payload)
    return JSONResponse({"status": "processing", "message": "Payload processing started in the background."})


async def _handle_generation(prompt: str, background_tasks: BackgroundTasks) -> FileResponse:
    prompt = prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="Prompt cannot be empty.")

    if len(prompt) > 2000:
        raise HTTPException(status_code=422, detail="Prompt too long (max 2000 chars).")

    if len(_pending_jobs) >= MAX_QUEUE:
        raise HTTPException(
            status_code=429,
            detail=f"Server busy – {MAX_QUEUE} jobs already queued. Try again later.",
        )

    log.info(f"🎬 New request → prompt: «{prompt[:60]}{'…' if len(prompt)>60 else ''}»")

    try:
        video_path = await _run_generation(prompt)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"Video generation failed: {e}")

    if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
        raise HTTPException(status_code=500, detail="Video file was not created.")

    # Keep the file (user wants it to persist in current folder)
    filename = f"grok_video_{uuid.uuid4().hex[:6]}.mp4"
    return FileResponse(
        path=video_path,
        media_type="video/mp4",
        filename=filename,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Prompt": prompt[:200],
        },
    )

# source venv/bin/activate
    #uvicorn server:app --host 0.0.0.0 --port 8000 --reload