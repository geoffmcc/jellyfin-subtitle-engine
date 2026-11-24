# Jellyfin Subtitle Engine

Generate English subtitles for Jellyfin movies and TV episodes using Faster-Whisper transcription, optional speech-to-English translation, and pyannote speaker-aware refinement. A resumable SQLite queue keeps track of work and a separate installer creates `.en.srt` sidecars without overwriting existing files.

> **Early standalone release:** Extracted from a working Ubuntu/WSL2 pipeline. The standalone installation and pinned dependency snapshots have **not** been validated on a fresh machine. Test with one item before processing your library.

## Features

- Find Jellyfin movies and episodes lacking detected English **text** subtitles
- Transcribe English speech; translate recognized non-English speech to English
- Timestamp words and refine subtitle boundaries around detected speaker changes
- Save finalized SRTs locally before installation
- Track completed, failed and interrupted jobs in SQLite; resume without redoing completed jobs
- Preview installation before writing; create sidecar files only if they do not exist

Existing English subtitle detection depends on Jellyfin's media-stream metadata, which can be incomplete. Generated captions and speaker identification may require manual review.

## Requirements

- Linux; developed with **Python 3.14** in Ubuntu on WSL2
- FFmpeg (including `ffprobe`) installed and on `PATH`
- Access to a Jellyfin server and a valid Jellyfin API token
- An NVIDIA GPU with compatible drivers for the **complete** speaker-aware pipeline
- Hugging Face token with accepted access terms for `pyannote/speaker-diarization-community-1`
- Local access to the media files **through a mounted directory**, for speaker diarization and optional subtitle installation

The included Python dependency lists are snapshots of two working WSL environments, **not guaranteed portable lockfiles**.

| Environment | Role | Core packages |
| --- | --- | --- |
| `.venv` | Subtitle queue and Faster-Whisper | `faster-whisper==1.2.1`, `ctranslate2==4.9.1`, CUDA 12 libraries |
| `.venv-diarization` | Pyannote subprocess | `pyannote-audio==4.1.1`, PyTorch, CUDA 13 libraries |

Both environments are required for the full pipeline. `--device cpu` changes only Faster-Whisper; **pyannote is still hardcoded to CUDA**.

## Install

Clone the source:

```bash
git clone https://github.com/geoffmcc/jellyfin-subtitle-engine.git
cd jellyfin-subtitle-engine
```

On Ubuntu, install FFmpeg and Python virtual-environment support (and make sure the correct NVIDIA drivers are already configured):

```bash
sudo apt update
sudo apt install ffmpeg python3-venv
python3 --version
ffmpeg -version
nvidia-smi
```

Create the two Python environments, using a compatible Python 3.14 interpreter:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-transcription.txt

python3 -m venv .venv-diarization
.venv-diarization/bin/python -m pip install -r requirements-diarization.txt
```

**Compatibility note:** These are environment snapshots from the original WSL setup; installation may fail on another Python/CUDA combination. Resolve those issues before running the application. The two CUDA runtime generations must remain isolated as configured.

For Faster-Whisper, the original CUDA 12 environment needed the Python-packaged cuBLAS and cuDNN libraries on the dynamic loader path. Activate the transcription environment and set the paths if your installation needs them:

```bash
source .venv/bin/activate
export LD_LIBRARY_PATH="$VIRTUAL_ENV/lib/python3.14/site-packages/nvidia/cublas/lib:$VIRTUAL_ENV/lib/python3.14/site-packages/nvidia/cudnn/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
```

The speaker subprocess deliberately removes `LD_LIBRARY_PATH` so its separate environment can load its own libraries. Adapt the paths if your venv layout differs.

## Configure Jellyfin and the media mount

Copy the example configuration and restrict its permissions:

```bash
cp .env.example .env
chmod 600 .env
```

Fill in your own values in `.env`:

```dotenv
JELLYFIN_URL=http://jellyfin.example:8096
JELLYFIN_TOKEN=your-own-api-token
JELLYFIN_USER_ID=your-jellyfin-user-id

MEDIA_SERVER_ROOT=/srv/media
MEDIA_MOUNT_ROOT=/mnt/media
```

**Media path mapping is important.** `MEDIA_SERVER_ROOT` is the prefix in video paths reported by the **Jellyfin server**, while `MEDIA_MOUNT_ROOT` is the corresponding media directory **as seen by the machine running this engine**. For example:

- Jellyfin reports: `/srv/media/Shows/Example/Episode.mkv`
- This machine sees: `/mnt/media/Shows/Example/Episode.mkv`

Both roots must be **absolute** and preserve the same relative folders. The local media mount must exist. The engine validates that mapped files remain within the configured mount, including symlinks. The mount needs read access for speaker detection; subtitle installation additionally needs permission to create sidecar files.

Set `JELLYFIN_USER_ID` explicitly for predictable library selection. If omitted, the code uses Jellyfin session discovery (optionally preferring `JELLYFIN_TV`), which may select an unintended user's library.

A Hugging Face access token is also required for speaker diarization. After accepting the terms for [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1), provide it **in the running shell**, not in source code:

```bash
read -rs 'HF_TOKEN?Hugging Face token: '
echo
export HF_TOKEN
```

## Use

### Activate the transcription environment

Every time you open a new terminal to run the subtitle engine, go into the project directory and activate the **transcription** virtual environment:

```bash
cd jellyfin-subtitle-engine
source .venv/bin/activate
```

Your shell prompt will typically show `(.venv)`. Verify that the active Python is from the project:

```bash
which python
python --version
```

All queue and installer commands below run from this environment. **Do not activate `.venv-diarization` manually for normal operation.** The engine starts its Python interpreter automatically in a separate subprocess when speaker detection is needed.

If you open a new terminal, activate `.venv` again and set `HF_TOKEN` again before running speaker-aware generation. If CUDA 12 libraries require the `LD_LIBRARY_PATH` setup shown in the installation section, apply that setup again in the new terminal.

To leave the environment later:

```bash
deactivate
```

### Scan — no transcription, no media writes

```bash
python jellyfin_subtitle_queue.py scan
```

The scan queries the Jellyfin inventory and estimates how many videos are missing English text subtitles. It does not modify media or generated subtitle files.

### Generate one item as an initial test

```bash
python -u jellyfin_subtitle_queue.py run --limit 1
```

This downloads/extracts audio, generates a draft SRT and word timestamps, runs the pyannote speaker-analysis subprocess, and finalizes a local SRT. **This stage does not install the result into the media library.** The GPU and mounted media access must both work.

After confirming the result, process more items:

```bash
python -u jellyfin_subtitle_queue.py run --all
```

The queue defaults to three items per run if neither `--all` nor `--limit` is specified.

**Processing order:** Eligible movies and TV series are sorted alphabetically (case-insensitive). Episodes within each TV series are processed by season number, then episode number. Existing completed jobs remain completed, so changing the order does **not** restart transcription or discard the SQLite queue. Episode ordering relies on the season and episode metadata returned by Jellyfin; items missing numbers are placed after numbered episodes within their series.

Review status and optionally retry failures:

```bash
python jellyfin_subtitle_queue.py status
python -u jellyfin_subtitle_queue.py run --retry-failed --limit 1
```

### Preview subtitle installation

```bash
python jellyfin_subtitle_install.py --limit 1
```

The installer validates the media mapping and reads the completed queue records. By default, it **previews** what would be created and writes nothing. With installation enabled, it also remembers handled jobs in a separate local SQLite database so later runs skip repeated `EXISTS` checks.

### Install completed subtitles

After confirming the media paths and the preview:

```bash
python jellyfin_subtitle_install.py --install --limit 1
```

To examine and install all completed subtitle jobs:

```bash
python jellyfin_subtitle_install.py --install --limit 0
```

The installer only creates missing `.en.srt` files with exclusive creation. It will not intentionally overwrite an existing sidecar, but a partially written new file can remain if the copy fails; inspect errors before retrying.

Installer state is stored in `subtitles/batch/subtitle_install_state.sqlite3`, separately from the transcription queue database. A job is marked handled only after an installation succeeds or its subtitle already exists. Preview mode does not mark any jobs handled. The `--limit` option now limits **unhandled** jobs examined, so new jobs are not starved behind previously installed ones.

If subtitles are removed or you need to verify previously handled jobs again, use `--recheck` (possibly with `--limit 0` to inspect all). This deliberately repeats the media checks:

```bash
python jellyfin_subtitle_install.py --recheck --limit 0
```

The state database is a local optimization, not an authoritative proof that a sidecar still exists. Avoid running multiple installers simultaneously.

### Optional queue settings

- `--device cuda|cpu` — selects the Faster-Whisper device (the speaker stage still uses CUDA)
- `--chunk-seconds N` — transcription chunk length (minimum 20 seconds)
- `--overlap N` — seconds of chunk overlap (0–10)
- `--retry-failed` — reattempt failed or no-speech jobs
- `--limit N` or `--all` — number of videos to process

## Files and privacy

Generated subtitles, job records and intermediate diarization artifacts live under `subtitles/` and are excluded from Git. The queue database is `subtitles/batch/subtitle_jobs.sqlite3`; installer bookkeeping is kept in `subtitles/batch/subtitle_install_state.sqlite3`.

The tool processes audio locally with downloaded speech models, but it **does** contact Jellyfin and uses Hugging Face for model downloads/authentication. Do not commit `.env`, `HF_TOKEN`, SRTs, database files, transcripts or media. Logs may include video titles and local media paths; review them before sharing publicly.

## Development tests

Media-path checks need no GPU, Jellyfin instance or extra packages:

```bash
python3 -m unittest discover -s tests -v
```

## Current limitations

- The standalone install is not yet verified on a clean Linux machine.
- Full speaker-aware processing requires a CUDA-capable NVIDIA GPU; CPU-only mode is incomplete.
- Dependency snapshots are tied to the original Python/CUDA environment.
- The system currently expects media files to be locally mountable for diarization and installation.
- Generated subtitles can contain recognition or speaker-boundary errors; test on one video before a bulk run.

## Temporary audio cleanup

Extracted audio is removed on normal completion, exceptions and ordinary interruption. Forced termination or older releases may leave audio behind. Stop all queue, installer and standalone speaker tools first, then preview the known disposable WAV files:

```bash
python jellyfin_subtitle_cleanup.py
```

To remove the listed files after confirming all processing tools are stopped:

```bash
python jellyfin_subtitle_cleanup.py --apply --offline
```

The utility takes the queue lock, skips symlinks and only recognizes specific WAV names in item work directories. Legacy standalone speaker tools do not share this lock, so the offline confirmation is required. It does not remove SRTs, manifests, JSON, databases, original media or arbitrary staging directories. Model caches are not included. Keep backups of your workspace as appropriate.

## Audio and speech outcomes

The queue probes the authenticated media source using ffprobe and checks the decoded mono 16 kHz PCM before loading the transcription model. Missing audio streams or empty decoded audio are recorded as `no_audio`; exact digital silence is recorded as `silent_audio`. Quiet nonzero samples are not discarded. Audible silence can contain nonzero samples (for example analog noise), so some apparently silent films still proceed to transcription and diarization.

`no_speech` means Whisper produced no usable cues. `no_speaker_turns` means diarization found no eligible turns; it does not prove the media is silent. Neither outcome publishes an SRT. Actual probing, model, authentication, decoding and subprocess errors remain `failed`. The queue's `status` and `scan` show these outcomes; ordinary runs skip them and `--retry-failed` explicitly retries them as well as failures. Existing database rows are not automatically reclassified. The scan's remaining estimate includes items without completed subtitles, including skipped outcomes.
