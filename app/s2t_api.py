import queue
import sys
import time
import numpy as np
import sounddevice as sd
import torch
import threading
from faster_whisper import WhisperModel
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

# -------------------------
# Timestamp helper
# -------------------------
def ts():
    return time.strftime("[%Y-%m-%d %H:%M:%S]")

def log(msg):
    print(f"{ts()} {msg}")
    sys.stdout.flush()

# -------------------------
# Load Silero VAD
# -------------------------
log("Loading Silero VAD...")
VAD_MODEL, utils = torch.hub.load(
    repo_or_dir="snakers4/silero-vad",
    model="silero_vad",
    trust_repo=True
)
(get_speech_timestamps, _, _, _, _) = utils

# -------------------------
# Load Whisper (Medium)
# -------------------------
log("Loading Whisper model...")
MODEL_SIZE = "medium"
DEVICE = "cpu"

model = WhisperModel(
    MODEL_SIZE,
    device=DEVICE,
    compute_type="int8"
)

# -------------------------
# Audio config
# -------------------------
SAMPLE_RATE = 16000
CHANNELS = 1
FRAME_DURATION = 0.5
BLOCKSIZE = int(SAMPLE_RATE * FRAME_DURATION)
SILENCE_TIMEOUT = 1.8
MIN_PHRASE_LENGTH = 0.8

audio_q = queue.Queue()

def audio_callback(indata, frames, time_info, status):
    audio_q.put(indata.copy())

# -------------------------
# Normalize audio
# -------------------------
def normalize_audio(a):
    a = a - np.mean(a)
    max_abs = np.max(np.abs(a)) + 1e-9
    a = a / max_abs
    return np.clip(a, -1.0, 1.0).astype(np.float32)

# -------------------------
# Record with VAD once per API call
# -------------------------
def record_with_vad():
    log("🎙️ Listening... speak now")

    frames = []
    silent_start = None
    speech_started = False
    start_time = time.time()
    FIRST_SPEECH_TIMEOUT = 10

    while True:
        try:
            chunk = audio_q.get(timeout=0.1)
        except queue.Empty:
            if not speech_started and time.time() - start_time > FIRST_SPEECH_TIMEOUT:
                log("⚠️ No speech detected.")
                return np.array([])
            continue

        chunk = chunk.flatten().astype(np.float32)

        speech_ts = get_speech_timestamps(
            torch.from_numpy(chunk),
            VAD_MODEL,
            sampling_rate=SAMPLE_RATE,
            threshold=0.3
        )

        if len(speech_ts) > 0:
            speech_started = True
            frames.append(chunk)
            silent_start = None
        else:
            if speech_started:
                if silent_start is None:
                    silent_start = time.time()
                elif time.time() - silent_start >= SILENCE_TIMEOUT:
                    log("⏸️ Silence detected, stopping.")
                    break

    if frames:
        audio_data = np.concatenate(frames)
        duration = len(audio_data) / SAMPLE_RATE
        if duration < MIN_PHRASE_LENGTH:
            log("⚠️ Speech too short.")
            return np.array([])
        return audio_data

    return np.array([])

# -------------------------
# Whisper transcription
# -------------------------
def run_transcription(audio):
    audio_norm = normalize_audio(audio)

    segments, _ = model.transcribe(
        audio_norm,
        language="en",
        beam_size=1,
        best_of=1,
        temperature=0,
        vad_filter=True
    )

    text = " ".join([s.text for s in segments]).strip()
    return text

# -------------------------
# FastAPI App
# -------------------------
app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/transcribe")
def api_transcribe():
    audio_q.queue.clear()

    input_info = sd.query_devices(kind="input")
    log(f"Using device: {input_info['name']}")

    log("Starting microphone...")
    with sd.InputStream(
        device=input_info["index"],
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        blocksize=BLOCKSIZE,
        dtype="float32",
        callback=audio_callback
    ):
        audio = record_with_vad()

    if len(audio) == 0:
        return JSONResponse({"text": "", "error": "no speech detected"}, status_code=400)

    text = run_transcription(audio)
    return {"text": text}

# -------------------------
# Run server
# -------------------------
if __name__ == "__main__":
    uvicorn.run("s2t_api:app", host="0.0.0.0", port=8000, reload=False)
