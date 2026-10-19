#!/usr/bin/env python3
"""
Transcribe a WAV file using the Parakeet TDT STT handler.

Usage:
    python transcribe_parakeet.py path/to/audio.wav
    python transcribe_parakeet.py path/to/audio.wav --device cpu
    python transcribe_parakeet.py path/to/audio.wav --model nvidia/parakeet-tdt-0.6b-v3
"""

import argparse
from queue import Queue
from threading import Event as ThreadingEvent

import numpy as np
import soundfile as sf


def load_audio(path: str) -> np.ndarray:
    """Load a WAV file, convert to mono and resample to 16 kHz."""
    audio, sr = sf.read(path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        from scipy import signal
        n = int(len(audio) * 16000 / sr)
        audio = signal.resample(audio, n)
    return audio.astype(np.float32)


def main():
    parser = argparse.ArgumentParser(description="Transcribe audio with Parakeet TDT")
    parser.add_argument("audio", help="Path to the WAV file")
    parser.add_argument(
        "--device", choices=["auto", "cpu", "cuda", "mps"], default="auto",
        help="Device to run inference on (default: auto)",
    )
    parser.add_argument(
        "--model", default=None,
        help="Model name (auto-selected based on device if omitted)",
    )
    args = parser.parse_args()

    audio = load_audio(args.audio)
    print(f"Loaded {args.audio}: {len(audio) / 16000:.2f}s, {len(audio)} samples @ 16kHz")

    from speech_to_speech.STT.parakeet_tdt_handler import ParakeetTDTSTTHandler
    from speech_to_speech.pipeline.messages import VADAudio

    stop_event = ThreadingEvent()
    handler = ParakeetTDTSTTHandler(
        stop_event=stop_event,
        queue_in=Queue(),
        queue_out=Queue(),
        setup_kwargs={"device": args.device, "model_name": args.model},
    )

    # Process the audio
    for output in handler.process(VADAudio(audio=audio)):
        if hasattr(output, "text"):
            print(f"\nTranscription: {output.text.strip()}")
            if hasattr(output, "language_code") and output.language_code:
                print(f"Language: {output.language_code}")
        else:
            print(f"Output type: {type(output).__name__} = {output}")

    handler.cleanup()


if __name__ == "__main__":
    main()
