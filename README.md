## Status

Standalone extraction of the working Jellyfin Subtitle Engine Version 7.2.

The source code has been separated from Jellyfin Companion. Installation and runtime validation on a standalone Ubuntu workstation are pending.

## Requirements

### System requirements

- Linux (originally developed under Ubuntu in WSL2)
- Python 3.14
- FFmpeg and FFprobe
- NVIDIA GPU with compatible drivers and CUDA support
- Jellyfin server with API access
- Hugging Face account and token for pyannote speaker diarization

### Python dependencies

The application uses two independent Python virtual environments to prevent conflicts between its transcription and diarization dependencies.

**Transcription environment (`.venv`)**

- Faster-Whisper 1.2.1
- CTranslate2 4.9.1
- NVIDIA CUDA 12 libraries
- Supporting dependencies listed in `requirements-transcription.txt`

**Diarization environment (`.venv-diarization`)**

- Pyannote Audio 4.1.1
- PyTorch and Torchaudio
- NVIDIA CUDA 13 libraries
- Supporting dependencies listed in `requirements-diarization.txt`

The dependency files reflect the working WSL installation. They have not yet been validated on a separate Ubuntu machine.

### Runtime environments

The main subtitle queue runs from `.venv`.

Speaker diarization runs in a separate subprocess using the Python interpreter from `.venv-diarization`.

The current implementation requires both environments to exist in the project directory.

### Configuration

The application reads Jellyfin configuration from `.env`. Copy `.env.example` to `.env`, supply the required values, and set restrictive permissions:

```bash
chmod 600 .env
```

Set `HF_TOKEN` in the environment before running speaker-aware subtitle generation.

Do not commit API tokens, generated subtitles, job databases, or media files to source control.
