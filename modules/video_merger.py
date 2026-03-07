import os
import subprocess
import logging
from typing import List
from pathlib import Path
from config import BASE_DIR, VIDEOS_DIR

log = logging.getLogger("GrokAPI.VideoMerger")

def merge_videos(story_id: str, video_paths: List[Path]) -> Path:
    """
    Merges a list of MP4 files sequentially into a single video file.
    Optionally layers a background audio (bg.mp3) if present in BASE_DIR.
    Returns the Path to the final merged file.
    """
    if not video_paths:
        raise ValueError("No video paths provided for merging.")
        
    concat_file = VIDEOS_DIR / f"concat_{story_id}.txt"
    bg_audio_path = BASE_DIR / "bg.mp3"
    has_bg_audio = bg_audio_path.exists()
    
    merged_output_name = f"temp_story_{story_id}_merged.mp4" if has_bg_audio else f"finalmergevideo_{story_id}.mp4"
    merged_output = VIDEOS_DIR / merged_output_name
    final_video_path = merged_output
    
    try:
        # Write concat list
        with open(concat_file, "w") as f:
            for vp in video_paths:
                f.write(f"file '{vp.absolute()}'\n")
                
        log.info(f"[story_id: {story_id}] 🎬 Merging {len(video_paths)} videos into {merged_output.name}")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", 
            "-i", str(concat_file.absolute()), 
            "-c", "copy", str(merged_output.absolute())
        ]
        
        process = subprocess.run(cmd, capture_output=True, text=True)
        
        if process.returncode != 0:
            log.error(f"[story_id: {story_id}] ❌ Failed to merge videos: {process.stderr}")
            raise RuntimeError(f"FFMPEG Merge failed: {process.stderr}")
            
        log.info(f"[story_id: {story_id}] ✅ Successfully merged videos to {merged_output}")
        
        # Add background audio if present
        if has_bg_audio:
            final_bg_output = VIDEOS_DIR / f"finalmergevideo_{story_id}.mp4"
            log.info(f"[story_id: {story_id}] 🎵 Adding background audio to {final_bg_output.name}")
            bg_cmd = [
                "ffmpeg", "-i", str(merged_output.absolute()), 
                "-stream_loop", "-1", 
                "-i", str(bg_audio_path.absolute()), 
                "-filter_complex", "[1:a]volume=0.3[a1];[0:a][a1]amix=inputs=2:duration=first:dropout_transition=2[a]", 
                "-map", "0:v", "-map", "[a]", 
                "-c:v", "copy", "-y", str(final_bg_output.absolute())
            ]
            
            bg_process = subprocess.run(bg_cmd, capture_output=True, text=True)
            
            if bg_process.returncode == 0:
                log.info(f"[story_id: {story_id}] ✅ Successfully added background audio to {final_bg_output}")
                final_video_path = final_bg_output
                try:
                    merged_output.unlink() # Cleanup intermediate merge
                except:
                    pass
            else:
                log.error(f"[story_id: {story_id}] ❌ Failed to add background audio: {bg_process.stderr}")
                raise RuntimeError(f"FFMPEG Audio mix failed: {bg_process.stderr}")
                
        return final_video_path
        
    finally:
        if concat_file.exists():
            try:
                concat_file.unlink()
            except:
                pass
