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
from app import generate_video

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

from typing import List

class ModulePayload(BaseModel):
    module_number: int
    video_generation_prompt: str

class StoryPayload(BaseModel):
    story_id: str
    modules: List[ModulePayload]

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
            result = await loop.run_in_executor(None, generate_video, prompt, IMAGE_PATH, output_path)
            
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


async def _process_payload_sequentially(payload: TestPayload):
    for story in payload.stories:
        # Sort modules by module_number to ensure strict sequential processing
        modules = sorted(story.modules, key=lambda m: m.module_number)
        
        for module in modules:
            prompt = module.video_generation_prompt
            success = False
            retries = 0
            max_retries = 3
            
            while retries <= max_retries and not success:
                log.info(f"[story_id: {story.story_id}] [module_number: {module.module_number}] video generation start (Attempt {retries + 1})")
                try:
                    video_path = await _run_generation(prompt)
                    log.info(f"[story_id: {story.story_id}] [module_number: {module.module_number}] success (file: {video_path})")
                    success = True
                except Exception as e:
                    log.error(f"[story_id: {story.story_id}] [module_number: {module.module_number}] failure: {e}")
                    if retries < max_retries:
                        log.info(f"[story_id: {story.story_id}] [module_number: {module.module_number}] retry attempt {retries + 1}")
                        await asyncio.sleep(5)
                    retries += 1
                    
            if not success:
                log.error(f"[story_id: {story.story_id}] stopped processing story due to module {module.module_number} failure after {max_retries} retries")
                break


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
async def process_test_payload(payload: TestPayload, background_tasks: BackgroundTasks):
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