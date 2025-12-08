import queue
import sys
import time
import numpy as np
import sounddevice as sd
import torch
import signal
import threading
from faster_whisper import WhisperModel

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
VAD_MODEL, utils = torch.hub.load(
    repo_or_dir="snakers4/silero-vad",
    model="silero_vad",
    trust_repo=True
)
(get_speech_timestamps, _, _, _, _) = utils

# -------------------------
# Load Whisper (Medium)
# -------------------------
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
# Interrupt control
# -------------------------
stop_flag = False
transcribing_flag = False

def handle_interrupt(sig, frame):
    global stop_flag
    stop_flag = True
    log("Ctrl+C received, stopping...")

signal.signal(signal.SIGINT, handle_interrupt)

# -------------------------
# Record with VAD
# -------------------------
def record_with_vad():
    log("🎙️ Listening... speak now")

    frames = []
    silent_start = None
    speech_started = False
    start_time = time.time()
    FIRST_SPEECH_TIMEOUT = 10

    while not stop_flag:
        try:
            chunk = audio_q.get(timeout=0.1)
        except queue.Empty:
            if stop_flag:
                return np.array([])

            if not speech_started and time.time() - start_time > FIRST_SPEECH_TIMEOUT:
                log("⚠️ No speech detected, retrying...")
                return np.array([])
            continue

        if stop_flag:   # ✅ FIX: instant exit
            return np.array([])

        chunk = chunk.flatten().astype(np.float32)

        # VAD
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
                    log("⏸️ Silence detected, stopping recording.")
                    break

    if stop_flag:
        return np.array([])

    if frames:
        audio_data = np.concatenate(frames)
        duration = len(audio_data) / SAMPLE_RATE
        if duration < MIN_PHRASE_LENGTH:
            log("⚠️ Speech too short, retrying...")
            return np.array([])
        return audio_data

    return np.array([])

# -------------------------
# Normalize audio
# -------------------------
def normalize_audio(a):
    a = a - np.mean(a)
    max_abs = np.max(np.abs(a)) + 1e-9
    a = a / max_abs
    return np.clip(a, -1.0, 1.0).astype(np.float32)

# -------------------------
# Background transcription
# -------------------------
def transcribe_background(audio_data, output_dict):
    global transcribing_flag

    if stop_flag:     # ✅ FIX: instant abort
        transcribing_flag = False
        return

    try:
        log("🔉 Transcription worker: started")
        audio_norm = normalize_audio(audio_data)

        segments, _ = model.transcribe(
            audio_norm,
            language="en",
            beam_size=1,          # ✅ FIX: MUCH faster
            best_of=1,            # ✅ FIX: MUCH faster
            temperature=0,
            vad_filter=True
        )

        text = " ".join([s.text for s in segments]).strip()
        output_dict["text"] = text
        log(f"✅ Transcription worker: finished ({len(text)} chars)")

    except Exception as e:
        output_dict["text"] = ""
        log(f"❌ Transcription error: {e}")

    finally:
        transcribing_flag = False

# -------------------------
# Correction dictionary
# -------------------------
CUSTOM_WORDS = {
    "sombra gate": "sompura gate",
    "somra": "sompura",
    "sombra": "sompura",
}

def post_correct(text):
    t = text.lower()
    for wrong, right in CUSTOM_WORDS.items():
        t = t.replace(wrong, right)
    return t

# -------------------------
# Main loop
# -------------------------
def main():
    global stop_flag, transcribing_flag

    log("🎤 Continuous Speech-to-Text (Ctrl+C to stop)")
    input_info = sd.query_devices(kind="input")
    log(f"🎧 Using device: {input_info['name']}")

    try:
        with sd.InputStream(
            device=input_info["index"],
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            blocksize=BLOCKSIZE,
            dtype="float32",
            callback=audio_callback
        ):
            while not stop_flag:
                audio = record_with_vad()
                if stop_flag:
                    break
                if len(audio) == 0:
                    continue

                log(f"📝 Captured audio ({len(audio)/SAMPLE_RATE:.2f}s). Starting transcription...")

                transcribing_flag = True
                output = {"text": ""}

                t = threading.Thread(target=transcribe_background, args=(audio, output), daemon=True)
                t.start()

                while transcribing_flag and not stop_flag:
                    time.sleep(0.05)

                if stop_flag:
                    log("🔻 Stop requested — abandoning transcription.")
                    break

                text = post_correct(output["text"])

                if text:
                    log(f"🗣️ You said: {text}")
                else:
                    log("⚠️ No speech recognized.")

    except KeyboardInterrupt:
        log("KeyboardInterrupt caught.")
    except Exception as e:
        log(f"Fatal error: {e}")

    log("🛑 Stopped by user.")
    time.sleep(0.05)
    sys.exit(0)

if __name__ == "__main__":
    main()
