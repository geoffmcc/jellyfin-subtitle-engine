# Jellyfin Subtitle Engine

GPU-accelerated subtitle generation for Jellyfin using Faster-Whisper, Pyannote speaker diarization, word-level alignment, and automatic SRT installation.

Jellyfin Subtitle Engine scans your media library for videos missing English text subtitles, generates new subtitles, refines subtitle boundaries around speaker changes, and optionally installs the resulting `.en.srt` files alongside your media.

## Features

- **Automatic transcription** using Faster-Whisper and NVIDIA GPU acceleration
- **Speech translation** to English for non-English audio
- **Speaker diarization** using Pyannote to detect speaker changes
- **Word-level timestamps** for more accurate subtitle timing
- **Speaker-aware refinement** to improve dialogue separation
- **Resumable processing queue** with SQLite job tracking
- **Safe subtitle installation** that avoids overwriting existing subtitle files
- **Read-only library scanning** to identify missing English subtitles

## Project Status

The current implementation is based on Version 7.2 of an actively used subtitle-processing pipeline originally developed under Ubuntu in WSL2.

The standalone code has been extracted, but installation and runtime behavior have not yet been validated on a fresh Linux machine.

**Important:** The current installer and speaker-processing modules still contain media-path mappings specific to the original environment. These must be adapted before using the project on a different system.

## Requirements

### System requirements

- Linux (developed under Ubuntu/WSL2)
- Python 3.14
- FFmpeg and FFprobe
- NVIDIA GPU with compatible drivers and CUDA support
- Jellyfin server with API access
- Hugging Face account and access token for Pyannote

### Python dependencies

Two separate Python virtual environments are used to isolate potentially conflicting dependencies.

| Environment | Primary components |
|---|---|
| `.venv` | Faster-Whisper 1.2.1, CTranslate2 4.9.1, CUDA 12 libraries |
| `.venv-diarization` | Pyannote Audio 4.1.1, PyTorch, Torchaudio, CUDA 13 libraries |

Dependency snapshots are provided in `requirements-transcription.txt` and `requirements-diarization.txt`. These reflect the original working installation and are not yet verified as portable dependency locks.

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/geoffmcc/jellyfin-subtitle-engine.git
cd jellyfin-subtitle-engine
```

### 2. Install system dependencies

On Ubuntu:

```bash
sudo apt update
sudo apt install -y ffmpeg python3-venv
```

Verify:

```bash
python3 --version
ffmpeg -version
nvidia-smi
```

Ensure the Python interpreter is compatible with the provided requirements and NVIDIA GPU drivers are installed correctly.

### 3. Create the transcription environment

```bash
python3 -m venv .venv
source .venv/bin/activate

python -m pip install -r requirements-transcription.txt
```

### 4. Create the diarization environment

```bash
deactivate

python3 -m venv .venv-diarization
.venv-diarization/bin/python -m pip install \
  -r requirements-diarization.txt
```

Dependency installation may require platform-specific adjustments. The existing dependency snapshots have not been verified on a fresh Ubuntu installation.

## Configuration

### 1. Configure Jellyfin

Create your local configuration:

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env`:

```dotenv
JELLYFIN_URL=http://your-jellyfin-server:8096
JELLYFIN_TOKEN=your-api-token
JELLYFIN_USER_ID=your-user-id
```

An appropriate Jellyfin API token is required.

### 2. Configure Hugging Face

Speaker diarization uses the `pyannote/speaker-diarization-community-1` model.

Ensure you have accepted its Hugging Face model access conditions, then set your access token:

```bash
read -rs 'HF_TOKEN?Hugging Face token: '
echo
export HF_TOKEN
```

The token must be available to the process that runs subtitle generation.

### 3. Configure media paths

The current code expects the Jellyfin server's media paths to be accessible through a local mounted media directory.

Before enabling processing, review the path mappings in:

- `speaker_finalize.py`
- `jellyfin_subtitle_install.py`

These files currently contain environment-specific path assumptions. A future update will move these settings into the application's configuration.

## Usage

Activate the transcription environment before running the commands:

```bash
source .venv/bin/activate
```

### Scan the Jellyfin library

Identify videos that may need English subtitles without generating new files:

```bash
python jellyfin_subtitle_queue.py scan
```

### View processing status

```bash
python jellyfin_subtitle_queue.py status
```

### Generate subtitles

Process up to three eligible videos (the default limit):

```bash
python -u jellyfin_subtitle_queue.py run
```

Process one video:

```bash
python -u jellyfin_subtitle_queue.py run --limit 1
```

Process all remaining eligible videos:

```bash
python -u jellyfin_subtitle_queue.py run --all
```

The queue records progress in SQLite and skips completed jobs when restarted.

Retry previously failed jobs:

```bash
python -u jellyfin_subtitle_queue.py run \
  --retry-failed --limit 1
```

### CPU transcription

The transcription queue also supports CPU inference:

```bash
python -u jellyfin_subtitle_queue.py run \
  --device cpu --limit 1
```

**Note:** This option changes the Faster-Whisper device only. The current Pyannote speaker-processing stage still requires CUDA, so it does not provide a fully CPU-only workflow.

## Installing subtitles

Generated subtitles are first saved locally beneath:

```text
subtitles/batch/by-item/
```

They are not automatically written into your media library by the queue.

Once the media-path mapping has been correctly configured, preview installation:

```bash
python jellyfin_subtitle_install.py --limit 1
```

Install the first eligible subtitle:

```bash
python jellyfin_subtitle_install.py --install --limit 1
```

Install all completed subtitles:

```bash
python jellyfin_subtitle_install.py --install --limit 0
```

The installer creates `.en.srt` sidecar files alongside their corresponding videos.

**Existing subtitle files are not overwritten.** The installer uses exclusive file creation to avoid replacing existing files.

## Processing and Data

The application maintains local processing data under `subtitles/`, including:

- SQLite job records
- Generated subtitle files
- Speaker-detection results
- Word-timing information
- Intermediate processing artifacts and backups

These files are excluded from Git version control.

## Known Limitations

- Standalone installation has not yet been validated on a fresh Ubuntu system.
- Some media paths remain hardcoded and require configuration changes.
- Speaker diarization currently requires CUDA.
- Two separate Python environments are required.
- Installation dependencies reflect the original development environment.
- Subtitle quality depends on audio clarity, language detection, word alignment, and speaker-detection accuracy.

## Privacy and Safety

The project communicates with a configured Jellyfin server and processes media audio locally using its installed speech models.

Jellyfin credentials, Hugging Face tokens, generated subtitle files, and processing databases should not be committed to source control.

Always verify the media-path configuration and perform a preview before enabling subtitle installation.

## Development

The project is under active development. Planned improvements include portable media-path configuration, reproducible installation, automated testing, and improved speaker-change detection.
