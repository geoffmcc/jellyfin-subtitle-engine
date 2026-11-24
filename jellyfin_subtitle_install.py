#!/usr/bin/env python3
"""Install completed Jellyfin Subtitle Engine files without overwriting anything.

Read-only by default. Configure MEDIA_SERVER_ROOT and MEDIA_MOUNT_ROOT.
Only creates matching *.en.srt sidecars; never deletes or edits existing files.
This script can run while subtitle queue generation continues.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from media_paths import media_destination, media_roots

HERE = Path(__file__).resolve().parent
DB = HERE / 'subtitles' / 'batch' / 'subtitle_jobs.sqlite3'
LOCAL = HERE / 'subtitles' / 'batch'


def candidate(row: sqlite3.Row):
    src_raw = row['srt_path']
    if not src_raw:
        raise ValueError('Missing local SRT path')
    src = Path(src_raw)
    if not src.is_absolute():
        src = HERE / src
    # Install only SRTs generated beneath the queue output directory.
    src_resolved = src.resolve(strict=True)
    if not src_resolved.is_relative_to(LOCAL.resolve()):
        raise ValueError('SRT is outside the local batch directory')
    if src.suffix.lower() != '.srt' or '.sample_' in src.name:
        raise ValueError('Not a complete SRT')
    if not src.is_file() or src.stat().st_size == 0:
        raise ValueError('Local SRT missing or empty')
    video, dest = media_destination(row['media_path'] or '')
    if not video.is_file():
        raise FileNotFoundError(f'Video not found through WSL mount: {video}')
    # Basic format check. Don't change text or infer content.
    with src.open('rb') as f:
        head = f.read(4096)
    if b'-->' not in head:
        raise ValueError('Local file does not look like an SRT')
    return src, video, dest


def exclusive_copy(source: Path, destination: Path):
    """Create ONLY a new sidecar. Incomplete copies are left for inspection."""
    copied = 0
    with source.open('rb') as reader:
        with destination.open('xb') as writer:
            while chunk := reader.read(1024 * 1024):
                writer.write(chunk)
                copied += len(chunk)
            writer.flush()
            os.fsync(writer.fileno())
    if copied != source.stat().st_size:
        raise OSError('Written byte count differs from source size; inspect destination')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='Actually create missing .en.srt sidecars (default: preview only)')
    parser.add_argument('--limit', type=int, default=10, help='Maximum completed jobs to examine (default 10, 0=all)')
    args = parser.parse_args()
    if args.limit < 0:
        parser.error('--limit must be >= 0')
    if not DB.is_file():
        parser.error(f'Queue database not found: {DB}')
    try:
        media_roots()
    except (RuntimeError, ValueError, OSError) as exc:
        parser.error(str(exc))
    print('INSTALL MODE: CREATE NEW .en.srt FILES ONLY' if args.install else 'PREVIEW ONLY: NO FILES WILL BE WRITTEN', flush=True)
    # Open database read-only so the transcription process remains independent.
    uri = f'file:{DB.as_posix()}?mode=ro'
    counts = dict(installable=0, exists=0, errors=0, installed=0)
    with sqlite3.connect(uri, uri=True, timeout=15) as conn:
        conn.row_factory = sqlite3.Row
        sql = "SELECT item_id,title,media_path,srt_path FROM jobs WHERE state='completed' ORDER BY updated_at,item_id"
        if args.limit:
            sql += f' LIMIT {args.limit:d}'
        for row in conn.execute(sql):
            try:
                src, video, dest = candidate(row)
                if dest.exists():
                    counts['exists'] += 1
                    print(f'EXISTS:  {dest} (unchanged)')
                    continue
                counts['installable'] += 1
                print(f'{"INSTALL" if args.install else "WOULD CREATE"}: {dest}\n  FROM: {src}', flush=True)
                if args.install:
                    try:
                        exclusive_copy(src, dest)
                        counts['installed'] += 1
                        print('  CREATED successfully', flush=True)
                    except FileExistsError:
                        counts['exists'] += 1
                        print('  SKIPPED: file appeared before copy', flush=True)
                    except Exception as err:
                        counts['errors'] += 1
                        print(f'  ERROR during copy (inspect target, no cleanup performed): {err}', file=sys.stderr, flush=True)
            except Exception as err:
                counts['errors'] += 1
                print(f'SKIP {row["title"]}: {err}', file=sys.stderr, flush=True)
    print('Summary: ' + ', '.join(f'{k}={v}' for k, v in counts.items()), flush=True)
    if not args.install:
        print('Preview only. Use --install --limit 1 to install the first eligible subtitle.')

if __name__ == '__main__':
    main()
