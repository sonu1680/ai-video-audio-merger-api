import os
import base64
import struct
from google import genai
from google.genai import types

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "./ai-video-generator-9f07b-e197aeaa0b1c.json"

PROJECT_ID = "ai-video-generator-9f07b"
LOCATION = os.environ.get("GOOGLE_CLOUD_REGION", "global")

# Initialize the Gemini client
client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION
)

def write_wav_buffer(pcm_buffer: bytes, channels: int = 1, sample_rate: int = 24000, bit_depth: int = 16) -> bytes:
    """Wraps raw PCM audio data into a valid WAV file format bytes object."""
    data_size = len(pcm_buffer)
    header_size = 44
    

    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        36 + data_size,
        b'WAVE',
        b'fmt ',
        16,
        1,
        channels,
        sample_rate,
        sample_rate * channels * bit_depth // 8,
        channels * bit_depth // 8,
        bit_depth,
        b'data',
        data_size
    )
    
    return header + pcm_buffer

def generate_speech(
    prompt: str,
    voice: str = "Algieba",
    locale: str = "hi-IN",
    style: str = "Deliver the story in an aggressive, high-impact, dominant cinematic voice. Speak fast and forcefully. Add vocal pressure and controlled aggression. Hit important words with strong emphasis and heavy breath support. Slight growl texture on emotional words. Maintain clarity in Hindi pronunciation. Build rising intensity toward the end. Keep pacing rapid but controlled. Make it sound powerful, dramatic, and emotionally explosive.",
    speed: float = 2.0,
    output_filename: str = "output.wav"
) -> bytes:
    """
    Generates speech from text using Google Gemini TTS.
    
    Args:
        prompt: The text prompt to convert to speech.
        voice: The voice to use.
        locale: The locale/language.
        style: Description of the narration style and emotion.
        speed: The speaking rate.
        output_filename: The file path to save the generated audio.
        
    Returns:
        The generated WAV bytes.
    """
    if not prompt:
        raise ValueError("A 'prompt' is required.")

    # Configure speech settings
    config_dict = {
        "temperature": 2.0,
        "speech_config": {
            "language_code": locale,
            "voice_config": {
                "prebuilt_voice_config": {
                    "voice_name": voice
                }
            }
        }
    }
    
    # Gemini Flash TTS rejects system_instruction in standard generate_content configs,
    # so we integrate the director's notes directly into the prompt text to guide the model.
    final_prompt = prompt
    if style or speed:
        instruction = "You are a professional voice actor. "
        if style:
            instruction += f"Read the following text in a {style} tone, matching the required emotion perfectly. "
        if speed and speed != 1.0:
            instruction += f"Speak at a {speed}x speaking rate. Pace your voice exactly to this speed multiplier. "
        instruction += "\n\nText to read:\n"
        
        final_prompt = instruction + prompt

    # Generate content
    response = client.models.generate_content(
        model='gemini-2.5-flash-tts',
        contents=final_prompt,
        config=config_dict
    )

    # Extract inline base64 audio data
    try:
        base64_audio = response.candidates[0].content.parts[0].inline_data.data
    except (IndexError, AttributeError) as e:
        raise RuntimeError(f"Failed to extract audio data from response: {e}")

    # Convert Base64 to PCM bytes
    if isinstance(base64_audio, str):
         pcm_buffer = base64.b64decode(base64_audio)
    else:
        # Sometimes the SDK decodes it automatically
        pcm_buffer = base64_audio

    # Convert PCM to properly formatted WAV audio
    wav_buffer = write_wav_buffer(pcm_buffer)

    # Save to file if path provided
    if output_filename:
        with open(output_filename, "wb") as f:
            f.write(wav_buffer)

    return wav_buffer

# Example usage:
if __name__ == "__main__":
    try:
        print("Generating speech...")
        wav_data = generate_speech(
            prompt="नमस्कार! यह एक परीक्षण है।",
            speed=1.5,
            output_filename="test_python.wav"
        )
        print("Speech generated successfully and saved to test_python.wav")
    except Exception as e:
        print(f"Error generating speech: {e}")
