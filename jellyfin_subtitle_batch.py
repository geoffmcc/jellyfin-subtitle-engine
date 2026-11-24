#!/usr/bin/env python3
"""Find Jellyfin movies/episodes lacking English text subtitles and generate SRTs.

Default is READ-ONLY inventory. Generation stores local files; --install additionally
writes sidecars through an explicit path mapping to a mounted media share.
Requires: config.py and its .env, faster-whisper, ffmpeg, CUDA libs for GPU.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config import read_env
from audio_outcomes import NoAudio, SilentAudio, check_pcm_audio

for k, v in read_env().items():
    os.environ.setdefault(k, v)

SERVER = os.getenv('JELLYFIN_URL', 'http://localhost:8096').rstrip('/')
TOKEN = os.getenv('JELLYFIN_TOKEN')
BASE = Path(__file__).resolve().parent
OUTDIR = BASE / 'subtitles' / 'batch'
TEXT_CODECS = {'subrip', 'srt', 'ass', 'ssa', 'webvtt', 'vtt', 'mov_text', 'text', 'sami', 'ttml', 'dfxp'}
ENGLISH = {'en', 'eng', 'english'}
VIDEO_EXTS = {'.mkv', '.mp4', '.m4v', '.avi', '.mov', '.ts', '.webm'}


def api(path, params=None):
    url = SERVER + path
    if params:
        url += '?' + urlencode(params)
    req = Request(url, headers={'Authorization': f'MediaBrowser Token="{TOKEN}"', 'Accept': 'application/json'})
    with urlopen(req, timeout=45) as r:
        return json.load(r)


def user_id():
    if os.getenv('JELLYFIN_USER_ID'):
        return os.environ['JELLYFIN_USER_ID']
    sessions = api('/Sessions')
    prefer = os.getenv('JELLYFIN_TV')
    for s in sorted(sessions, key=lambda s: s.get('DeviceName') != prefer):
        if s.get('UserId'):
            return s['UserId']
    raise SystemExit('No Jellyfin user ID in /Sessions. Set JELLYFIN_USER_ID in .env.')


def inventory(uid):
    offset = 0
    while True:
        page = api(f'/Users/{uid}/Items', {
            'Recursive': 'true', 'IncludeItemTypes': 'Movie,Episode',
            'Fields': 'Path,MediaSources,MediaStreams',
            'StartIndex': offset, 'Limit': 200,
        })
        items = page.get('Items', [])
        for item in items:
            if item.get('IsFolder') or item.get('Type') not in ('Movie', 'Episode'):
                continue
            yield item
        offset += len(items)
        if not items or offset >= page.get('TotalRecordCount', offset):
            return


def subtitle_streams(item):
    streams = list(item.get('MediaStreams') or [])
    for ms in item.get('MediaSources') or []:
        streams.extend(ms.get('MediaStreams') or [])
    unique = {}
    for s in streams:
        if s.get('Type', '').casefold() == 'subtitle':
            unique[(s.get('Index'), s.get('Codec'), s.get('Language'), s.get('IsExternal'))] = s
    return list(unique.values())


def english_text_present(item):
    for s in subtitle_streams(item):
        codec = (s.get('Codec') or '').casefold()
        lang = (s.get('Language') or '').casefold()
        if lang in ENGLISH and codec in TEXT_CODECS:
            return True
    return False


def fmt(seconds):
    ms = max(0, round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def make_clip(audio, clip, start, end):
    # First extraction is PCM mono 16 kHz. Avoid re-reading the video for each chunk.
    with wave.open(str(audio), 'rb') as src:
        sample_rate = src.getframerate()
        if src.getnchannels() != 1 or src.getsampwidth() != 2:
            raise RuntimeError('Unexpected WAV format')
        src.setpos(min(src.getnframes(), round(start * sample_rate)))
        frames = src.readframes(max(0, round((end - start) * sample_rate)))
    with wave.open(str(clip), 'wb') as dst:
        dst.setnchannels(1)
        dst.setsampwidth(2)
        dst.setframerate(sample_rate)
        dst.writeframes(frames)


def probe_audio(source, auth):
    result = subprocess.run(['ffprobe', '-v', 'error', '-headers', auth,
                             '-select_streams', 'a:0', '-show_entries', 'stream=index',
                             '-of', 'json', source], check=True, capture_output=True, text=True)
    streams = json.loads(result.stdout)['streams']
    if not isinstance(streams, list):
        raise ValueError('Invalid ffprobe stream response')
    if not streams:
        raise NoAudio('Media has no audio stream')


def convert(item, english_model, translation_model, chunk_seconds, overlap, dest, word_output=None, *, english_factory=None):
    source = SERVER + f'/Items/{item["Id"]}/File'
    auth = f'Authorization: MediaBrowser Token="{TOKEN}"\r\n'
    probe_audio(source, auth)
    with tempfile.TemporaryDirectory(prefix='jellyfin-batch-srt-') as tmp:
        audio, clip = Path(tmp) / 'audio.wav', Path(tmp) / 'clip.wav'
        command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                   '-headers', auth, '-i', source, '-map', '0:a:0',
                   '-vn', '-ac', '1', '-ar', '16000', '-acodec', 'pcm_s16le', '-y', str(audio)]
        subprocess.run(command, check=True)
        check_pcm_audio(audio)
        if english_factory is not None:
            english_model = english_factory()
        with wave.open(str(audio), 'rb') as f:
            duration = f.getnframes() / f.getframerate()
        records = []
        all_timed_words = []
        total = (int(duration - 1e-6) // chunk_seconds) + 1
        for n in range(total):
            core_start = n * chunk_seconds
            core_end = min(duration, (n + 1) * chunk_seconds)
            start, end = max(0, core_start - overlap), min(duration, core_end + overlap)
            make_clip(audio, clip, start, end)
            # Detect each chunk's language, as in the previous multilingual worker.
            # The English/turbo model is a recognition pass, not a translator.
            segments, info = english_model.transcribe(
                str(clip), language=None, task='transcribe', beam_size=5,
                vad_filter=False, condition_on_previous_text=False,
                word_timestamps=True)
            language = (info.language or 'unknown').lower()
            confidence = float(info.language_probability or 0)
            if language not in ('en', 'unknown'):
                print(f'  detected {language} (confidence {confidence:.2f}); '
                      'translating speech to English with large-v3', flush=True)
                segments, _ = translation_model().transcribe(
                    str(clip), language=language, task='translate', beam_size=5,
                    vad_filter=False, condition_on_previous_text=False,
                    word_timestamps=True)
            elif confidence < 0.55:
                print(f'  uncertain language detection: {language} ({confidence:.2f}); '
                      'review this chunk', flush=True)
            words = []
            for seg in segments:
                for w in seg.words or []:
                    if w.start is None or w.end is None:
                        continue
                    a, b = start + float(w.start), start + float(w.end)
                    mid = (a + b) / 2
                    if core_start <= mid < core_end or (n == total - 1 and mid <= core_end):
                        if w.word.strip() and b > a:
                            words.append((a, b, w.word.strip()))
            words.sort(key=lambda w: (w[0], w[1]))
            if word_output is not None:
                all_timed_words.extend({'start': a, 'end': b, 'text': text}
                                       for a,b,text in words)
            group = []
            for word in words:
                if group:
                    gap = word[0] - group[-1][1]
                    span = word[1] - group[0][0]
                    chars = sum(len(w[2]) + 1 for w in group)
                    if gap > 1.2 or span > 6.0 or chars > 70:
                        records.append([group[0][0], group[-1][1], ' '.join(w[2] for w in group)])
                        group = []
                group.append(word)
            if group:
                records.append([group[0][0], group[-1][1], ' '.join(w[2] for w in group)])
            print(f'  chunk {n + 1}/{total}, {len(records)} captions', flush=True)

        records.sort(key=lambda x: (x[0], x[1]))
        normalized = []
        for start, end, text in records:
            if normalized and end <= normalized[-1][1] and text == normalized[-1][2]:
                continue
            # Resolve overlapping captions without modifying the audio transcript.
            if normalized and start < normalized[-1][1]:
                if start - normalized[-1][0] >= 0.3:
                    normalized[-1][1] = max(normalized[-1][0] + 0.05, start - 0.001)
                else:
                    start = normalized[-1][1] + 0.001
            if end > start and text:
                normalized.append([start, end, text])
        if not normalized:
            print('  no speech recognized; no SRT written', flush=True)
            return 0
        part = dest.with_suffix('.srt.partial')
        try:
            with part.open('w', encoding='utf-8') as f:
                for i, (a, b, text) in enumerate(normalized, 1):
                    f.write(f'{i}\n{fmt(a)} --> {fmt(b)}\n{text}\n\n')
            part.replace(dest)
        except BaseException:
            print(f'  partial result retained: {part}', file=sys.stderr)
            raise
        if word_output is not None:
            # Completed only after a valid SRT has been produced. The queue uses
            # this private JSON before publishing a completed job.
            with Path(word_output).open('x', encoding='utf-8') as f:
                json.dump({'words': all_timed_words, 'failed_ranges': [],
                           'audio_duration': duration}, f, ensure_ascii=False)
        return len(normalized)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['scan', 'generate'])
    p.add_argument('--limit', type=int, default=1, help='Maximum items to generate (default 1)')
    p.add_argument('--chunk-seconds', type=int, default=30)
    p.add_argument('--overlap', type=float, default=3)
    p.add_argument('--install', action='store_true', help='Copy SRT to explicitly mapped writable media share')
    p.add_argument('--server-root', help='Jellyfin server-side media root (e.g. /media)')
    p.add_argument('--mount-root', help='WSL path to mounted same media root (e.g. /mnt/media)')
    p.add_argument('--device', choices=['cuda', 'cpu'], default='cuda')
    args = p.parse_args()
    if args.limit < 1 or args.chunk_seconds < 20 or not 0 <= args.overlap <= 10:
        p.error('Invalid --limit, --chunk-seconds or --overlap')
    if args.install and (not args.server_root or not args.mount_root):
        p.error('--install requires both --server-root and --mount-root')
    if not TOKEN:
        p.error('Missing JELLYFIN_TOKEN in .env')
    uid = user_id()
    items = list(inventory(uid))
    missing = [x for x in items if not english_text_present(x)]
    print(f'Library: {len(items)} videos; {len(missing)} lack detected English text subtitles.')
    for item in missing[:30 if args.action == 'scan' else args.limit]:
        print(f'  {item.get("Type")} | {item.get("SeriesName", "")} {item.get("Name")} | {item.get("Path", "(no path)")}')
    if args.action == 'scan':
        if len(missing) > 30:
            print(f'  ... and {len(missing) - 30} more')
        print('Scan only. Nothing modified. Note: subtitle metadata may be incomplete for some items.')
        return
    OUTDIR.mkdir(parents=True, exist_ok=True)
    selected = missing[:args.limit]
    if not selected:
        print('No candidates.'); return
    if args.install:
        root = Path(args.server_root)
        local = Path(args.mount_root)
        if not root.is_absolute() or not local.is_absolute() or not local.is_dir():
            p.error('Both media roots must be absolute; --mount-root must exist')
    from faster_whisper import WhisperModel
    model = WhisperModel('mobiuslabsgmbh/faster-whisper-large-v3-turbo',
                         device=args.device, compute_type='float16' if args.device == 'cuda' else 'int8')
    translated_model = None
    def translator():
        nonlocal translated_model
        if translated_model is None:
            translated_model = WhisperModel('large-v3', device=args.device,
                                            compute_type='float16' if args.device == 'cuda' else 'int8')
        return translated_model
    errors = 0
    for i, item in enumerate(selected, 1):
        name = re.sub(r'[^a-zA-Z0-9._-]+', '_', item.get('Name', 'Untitled')).strip('._')[:90]
        target = OUTDIR / f'{name}_{item["Id"][:12]}.en.srt'
        print(f'[{i}/{len(selected)}] {item.get("Name")}', flush=True)
        sidecar = None
        if args.install:
            original = Path(item.get('Path') or '')
            try:
                rel = original.relative_to(root)
            except ValueError:
                print(f'  SKIP: path not inside {root}: {original}')
                continue
            if original.suffix.lower() not in VIDEO_EXTS:
                print('  SKIP: unrecognized media file extension')
                continue
            video = local / rel
            # Never write into the library unless the actual media path matches.
            if not video.is_file():
                print(f'  SKIP: video not found in mount: {video}')
                continue
            sidecar = video.with_name(video.stem + '.en.srt')
            if sidecar.exists():
                print(f'  SKIP: sidecar already exists: {sidecar}')
                continue
        if target.exists():
            print(f'  local SRT already exists: {target}')
        else:
            try:
                count = convert(item, model, translator, args.chunk_seconds, args.overlap, target)
                print(f'  saved {count} captions: {target}', flush=True)
            except (NoAudio, SilentAudio) as exc:
                print(f'  SKIP: {exc}', flush=True)
                continue
            except Exception as exc:
                errors += 1
                print(f'  FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
                continue
        if sidecar and target.exists():
            # Exclusive creation prevents overwriting any existing subtitle, including races.
            try:
                with sidecar.open('xb') as dst, target.open('rb') as src:
                    shutil.copyfileobj(src, dst)
                print(f'  installed: {sidecar}')
            except FileExistsError:
                print(f'  SKIP: subtitle appeared during run: {sidecar}')
            except Exception as exc:
                errors += 1
                print(f'  INSTALL FAILED: {exc}', file=sys.stderr)
    if errors:
        raise SystemExit(f'Completed with {errors} errors')


if __name__ == '__main__':
    main()
