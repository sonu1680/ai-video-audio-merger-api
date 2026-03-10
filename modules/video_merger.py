import os
import subprocess
import logging
from typing import List
from pathlib import Path
from config import BASE_DIR, VIDEOS_DIR

log = logging.getLogger("GrokAPI.VideoMerger")

def merge_videos(story_id: str, video_paths: List[Path], voiceover_path: Path = None) -> Path:
    """
    Merges a list of MP4 files sequentially into a single video file.
    Optionally layers a voiceover (.wav) and a background audio (bg.mp3).
    Returns the Path to the final merged file.
    """
    if not video_paths:
        raise ValueError("No video paths provided for merging.")
        
    concat_file = VIDEOS_DIR / f"concat_{story_id}.txt"
    bg_audio_path = BASE_DIR / "bg.mp3"
    
    has_bg_audio = bg_audio_path.exists()
    has_voiceover = voiceover_path is not None and Path(voiceover_path).exists()
    
    # We always start with a base merge
    base_merged_output = VIDEOS_DIR / f"temp_story_{story_id}_merged.mp4"
    final_video_path = base_merged_output
    
    try:
        # Write concat list
        with open(concat_file, "w") as f:
            for vp in video_paths:
                f.write(f"file '{vp.absolute()}'\n")
                
        log.info(f"[story_id: {story_id}] 🎬 Merging {len(video_paths)} videos into {base_merged_output.name}")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", 
            "-i", str(concat_file.absolute()), 
            "-c", "copy", str(base_merged_output.absolute())
        ]
        
        process = subprocess.run(cmd, capture_output=True, text=True)
        
        if process.returncode != 0:
            log.error(f"[story_id: {story_id}] ❌ Failed to merge videos: {process.stderr}")
            raise RuntimeError(f"FFMPEG Merge failed: {process.stderr}")
            
        log.info(f"[story_id: {story_id}] ✅ Successfully merged videos to {base_merged_output}")
        
        # Audio Mixing Stage
        if has_bg_audio or has_voiceover:
            final_audio_output = VIDEOS_DIR / f"finalmergevideo_{story_id}.mp4"
            log.info(f"[story_id: {story_id}] 🎵 Adding requested audio tracks (Voiceover: {has_voiceover}, BG: {has_bg_audio})")
            
            audio_cmd = ["ffmpeg", "-y", "-i", str(base_merged_output.absolute())]
            filter_complex = []
            inputs_count = 1 # Video's own audio (if any exists, usually Grok is silent, but we count it)
            
            # Add inputs
            if has_voiceover:
                audio_cmd.extend(["-i", str(voiceover_path.absolute())])
                vo_idx = inputs_count
                inputs_count += 1
                filter_complex.append(f"[{vo_idx}:a]volume=1.0[a_vo];")
                
            if has_bg_audio:
                audio_cmd.extend(["-stream_loop", "-1", "-i", str(bg_audio_path.absolute())])
                bg_idx = inputs_count
                inputs_count += 1
                filter_complex.append(f"[{bg_idx}:a]volume=0.3[a_bg];")
                
            # Build the amix string explicitly depending on presence
            amix_inputs = "[0:a]" # Base video audio
            if has_voiceover:
                amix_inputs += "[a_vo]"
            if has_bg_audio:
                amix_inputs += "[a_bg]"
                
            filter_complex.append(f"{amix_inputs}amix=inputs={inputs_count}:duration=first:dropout_transition=2[a]")
            
            audio_cmd.extend([
                "-filter_complex", "".join(filter_complex),
                "-map", "0:v", "-map", "[a]",
                "-c:v", "copy", 
                str(final_audio_output.absolute())
            ])
            
            bg_process = subprocess.run(audio_cmd, capture_output=True, text=True)
            
            if bg_process.returncode == 0:
                log.info(f"[story_id: {story_id}] ✅ Successfully mixed audio to {final_audio_output}")
                final_video_path = final_audio_output
                try:
                    base_merged_output.unlink() # Cleanup intermediate merge
                except:
                    pass
            else:
                log.error(f"[story_id: {story_id}] ❌ Failed to mix audio: {bg_process.stderr}")
                raise RuntimeError(f"FFMPEG Audio mix failed: {bg_process.stderr}")
        else:
            # Rename the base if no audio processing was done
            final_renamed = VIDEOS_DIR / f"finalmergevideo_{story_id}.mp4"
            os.rename(base_merged_output, final_renamed)
            final_video_path = final_renamed
                
        return final_video_path
        
    finally:
        if concat_file.exists():
            try:
                concat_file.unlink()
            except:
                pass
