"""Shared non-publication outcomes for queue and speaker subprocesses."""
import wave

NO_SPEAKER_EXIT = 42
RETRY_STATES = frozenset({"failed", "no_speech", "no_audio", "silent_audio", "no_speaker_turns"})

class NoAudio(Exception):
    pass

class SilentAudio(Exception):
    pass

class NoSpeakerTurns(ValueError):
    pass

def check_pcm_audio(path):
    """Reject only exact digital silence in decoded mono 16-bit PCM.

    Quiet nonzero samples are intentionally not classified as silence.
    Read bounded blocks rather than loading the entire recording.
    """
    with wave.open(str(path), "rb") as f:
        if f.getnchannels() != 1 or f.getsampwidth() != 2 or f.getframerate() != 16000:
            raise ValueError("Unexpected extracted PCM format")
        if f.getnframes() == 0:
            raise NoAudio("Audio stream contains no decoded samples")
        remaining = f.getnframes()
        nonzero = False
        while remaining:
            data = f.readframes(min(remaining, 65536))
            if not data or len(data) % 2:
                raise ValueError("Truncated PCM audio")
            remaining -= len(data) // 2
            nonzero |= any(data)
        if not nonzero:
            raise SilentAudio("Decoded audio is digitally silent")
