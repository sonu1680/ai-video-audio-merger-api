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
    voiceover: Optional[str] = None

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
    
    from modules.video_processor import generate_modules_sequentially
    from modules.video_merger import merge_videos
    from modules.video_uploader import upload_video_to_r2
    from modules.webhook_sender import send_n8n_webhook
    from modules.voiceover import generate_speech
    import datetime

    for story in stories:
        current_story_id = story.story_id if story.story_id is not None else story.id
        # Sort modules by module_number to ensure strict sequential processing
        modules = sorted(story.modules, key=lambda m: m.module_number)
        
        async with _chrome_lock:
            # We hold the lock for the entire story so the browser session is isolated.
            loop = asyncio.get_event_loop()
            
            try:
                # 1. Generate Videos
                def _run_generation_task():
                    # We pass model dictionaries rather than Pydantic objects since sync playwright runs in another thread easily
                    module_dicts = [m.dict() for m in modules]
                    return generate_modules_sequentially(str(current_story_id), module_dicts)
                
                generated_video_paths = await loop.run_in_executor(None, _run_generation_task)
                
                if not generated_video_paths:
                    log.warning(f"[story_id: {current_story_id}] ⚠️ No videos generated, skipping merge.")
                    continue
                    
                # 1.5 Generate Voiceover
                full_voiceover_text = " ".join([m.voiceover.strip() for m in modules if m.voiceover and m.voiceover.strip()])
                voiceover_path = None
                
                if full_voiceover_text:
                    log.info(f"[story_id: {current_story_id}] 🎙️ Generating voiceover track for {len(modules)} modules")
                    vo_filename = VIDEOS_DIR / f"voiceover_{current_story_id}.wav"
                    
                    def _run_voiceover_task():
                        generate_speech(
                            prompt=full_voiceover_text,
                            output_filename=str(vo_filename)
                        )
                        return vo_filename
                        
                    try:
                        voiceover_path = await loop.run_in_executor(None, _run_voiceover_task)
                        log.info(f"[story_id: {current_story_id}] ✅ Voiceover saved to {voiceover_path}")
                    except Exception as ve:
                        log.error(f"[story_id: {current_story_id}] ❌ Voiceover generation failed: {ve}")
                        # We continue even if voiceover fails to at least give the video
                
                # 2. Merge Videos
                def _run_merge_task():
                    return merge_videos(str(current_story_id), generated_video_paths, voiceover_path)
                    
                final_video_path = await loop.run_in_executor(None, _run_merge_task)
                
                # 3. Upload to R2
                timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                bucket_filename = f"videos/video_{timestamp_str}.mp4"
                
                log.info(f"[story_id: {current_story_id}] ☁️ Uploading {final_video_path.name} to R2 bucket as {bucket_filename}...")
                
                def _run_upload_task():
                     return upload_video_to_r2(str(final_video_path.absolute()), bucket_filename)
                     
                upload_success = await loop.run_in_executor(None, _run_upload_task)
                
                if upload_success:
                    # 4. Send Webhook
                    def _run_webhook_task():
                        return send_n8n_webhook(
                            str(current_story_id), 
                            bucket_filename, 
                            timestamp_str, 
                            source_video_path=str(final_video_path.absolute())
                        )
                    
                    await loop.run_in_executor(None, _run_webhook_task)
                    
            except Exception as e:
                log.error(f"[story_id: {current_story_id}] ❌ Sequence failed: {e}")


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

# ─────────────────────────── DEBUG ENDPOINTS ──────────────────────────────────

class WebhookTestPayload(BaseModel):
    story_id: str
    bucket_filename: str
    timestamp_str: str
    source_video_path: Optional[str] = None

@app.post("/api/test_webhook", summary="Test the n8n webhook module independently")
async def test_webhook(payload: WebhookTestPayload, background_tasks: BackgroundTasks):
    from modules.webhook_sender import send_n8n_webhook
    def _run_test():
        send_n8n_webhook(
            payload.story_id, 
            payload.bucket_filename, 
            payload.timestamp_str,
            source_video_path=payload.source_video_path
        )
    background_tasks.add_task(_run_test)
    return JSONResponse({"status": "queued", "message": "Webhook test triggered."})

class UploadTestPayload(BaseModel):
    file_path: str
    bucket_filename: Optional[str] = None

@app.post("/api/test_upload", summary="Test the R2 upload module independently")
async def test_upload(payload: UploadTestPayload, background_tasks: BackgroundTasks):
    from modules.video_uploader import upload_video_to_r2
    def _run_test():
        upload_video_to_r2(payload.file_path, payload.bucket_filename)
    background_tasks.add_task(_run_test)
    return JSONResponse({"status": "queued", "message": "Upload test triggered."})

class MergeTestPayload(BaseModel):
    story_id: str
    video_filenames: List[str]

@app.post("/api/test_merge", summary="Test the video merging module independently")
async def test_merge(payload: MergeTestPayload, background_tasks: BackgroundTasks):
    from modules.video_merger import merge_videos
    from pathlib import Path
    
    def _run_test():
        try:
            paths = [VIDEOS_DIR / f for f in payload.video_filenames]
            for p in paths:
                if not p.exists():
                    log.error(f"Missing file: {p}")
                    return
            merge_videos(payload.story_id, paths)
        except Exception as e:
            log.error(f"Test merge error: {e}")
            
    background_tasks.add_task(_run_test)
    return JSONResponse({"status": "queued", "message": "Merge test triggered."})