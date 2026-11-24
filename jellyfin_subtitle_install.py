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
STATE_DB = LOCAL / 'subtitle_install_state.sqlite3'


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



def open_state(writable: bool) -> sqlite3.Connection:
    """Separate bookkeeping DB; never modify the transcription queue database."""
    if writable:
        db = sqlite3.connect(STATE_DB, timeout=15)
    elif STATE_DB.exists():
        return sqlite3.connect(f'file:{STATE_DB.as_posix()}?mode=ro', uri=True, timeout=15)
    else:
        db = sqlite3.connect(':memory:')
    db.execute('''CREATE TABLE IF NOT EXISTS handled (
        item_id TEXT PRIMARY KEY,
        updated_at TEXT NOT NULL,
        media_path TEXT NOT NULL,
        srt_path TEXT NOT NULL
    )''')
    db.commit()
    return db


def already_handled(db: sqlite3.Connection, row: sqlite3.Row) -> bool:
    record = db.execute(
        'SELECT 1 FROM handled WHERE item_id=? AND updated_at=? AND media_path=? AND srt_path=?',
        (row['item_id'], row['updated_at'], row['media_path'], row['srt_path'])
    ).fetchone()
    return record is not None


def mark_handled(db: sqlite3.Connection, row: sqlite3.Row) -> None:
    db.execute('''INSERT INTO handled(item_id, updated_at, media_path, srt_path)
                  VALUES (?, ?, ?, ?)
                  ON CONFLICT(item_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    media_path=excluded.media_path,
                    srt_path=excluded.srt_path''',
               (row['item_id'], row['updated_at'], row['media_path'], row['srt_path']))
    db.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='Actually create missing .en.srt sidecars (default: preview only)')
    parser.add_argument('--limit', type=int, default=10, help='Maximum unhandled completed jobs to examine (default 10, 0=all)')
    parser.add_argument('--recheck', action='store_true', help='Recheck previously handled jobs (e.g. if subtitles were removed)')
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
    # The transcription database remains read-only; installation state is separate.
    uri = f'file:{DB.as_posix()}?mode=ro'
    counts = dict(installable=0, exists=0, errors=0, installed=0, previously_handled=0)
    examined = 0
    with sqlite3.connect(uri, uri=True, timeout=15) as conn, open_state(args.install) as state:
        conn.row_factory = sqlite3.Row
        sql = ("SELECT item_id,title,media_path,srt_path,updated_at FROM jobs "
               "WHERE state='completed' ORDER BY updated_at,item_id")
        for row in conn.execute(sql):
            if not args.recheck and already_handled(state, row):
                counts['previously_handled'] += 1
                continue
            if args.limit and examined >= args.limit:
                break
            examined += 1
            try:
                src, video, dest = candidate(row)
                if dest.exists():
                    counts['exists'] += 1
                    print(f'EXISTS:  {dest} (unchanged)')
                    if args.install:
                        mark_handled(state, row)
                    continue
                counts['installable'] += 1
                print(f'{"INSTALL" if args.install else "WOULD CREATE"}: {dest}\\n  FROM: {src}', flush=True)
                if args.install:
                    try:
                        exclusive_copy(src, dest)
                        counts['installed'] += 1
                        mark_handled(state, row)
                        print('  CREATED successfully', flush=True)
                    except FileExistsError:
                        counts['exists'] += 1
                        mark_handled(state, row)
                        print('  SKIPPED: file appeared before copy', flush=True)
                    except Exception as err:
                        counts['errors'] += 1
                        print(f'  ERROR during copy (inspect target, no cleanup performed): {err}', file=sys.stderr, flush=True)
            except Exception as err:
                counts['errors'] += 1
                print(f'SKIP {row["title"]}: {err}', file=sys.stderr, flush=True)
    print('Summary: ' + ', '.join(f'{k}={v}' for k, v in counts.items()) + f', examined={examined}', flush=True)
    if not args.install:
        print('Preview only. Use --install --limit 1 to install the first eligible subtitle.')


if __name__ == '__main__':
    main()
