#!/usr/bin/env python3
"""Safe, resumable, LOCAL-ONLY Jellyfin English subtitle queue.

Requires jellyfin_subtitle_batch.py and speaker_auto_finalize.py. Never edits
media or existing subtitles. New local SRTs are published only after V7 succeeds.
Run `python -u jellyfin_subtitle_queue.py run --all` for entire candidate library.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import re
import sqlite3
import tempfile
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import jellyfin_subtitle_batch as batch

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'subtitles' / 'batch'
DB = OUT / 'subtitle_jobs.sqlite3'


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def database():
    OUT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=30000')
    db.execute('''CREATE TABLE IF NOT EXISTS jobs (
        item_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        media_path TEXT,
        state TEXT NOT NULL,
        srt_path TEXT,
        captions INTEGER,
        attempts INTEGER NOT NULL DEFAULT 0,
        error TEXT,
        updated_at TEXT NOT NULL
    )''')
    db.commit()
    return db


def filename(item):
    """Keep the exact video basename, with a Jellyfin-recognized .en.srt suffix.

    Store each item in its own ID directory so identical names from different
    seasons/directories cannot collide. The media share is not touched.
    """
    video = Path(item.get('Path') or '')
    if not video.name or not video.suffix:
        raise ValueError(f'Jellyfin item has no usable video path: {item.get("Id")}')
    return OUT / 'by-item' / item['Id'] / (video.stem + '.en.srt')


def migrate_completed(db, item, dest, row):
    """Copy previously generated subtitles to the new path. Never remove old files."""
    if not row or row['state'] != 'completed' or not row['srt_path']:
        return False
    old = Path(row['srt_path'])
    if not old.is_file():
        return False
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with dest.open('xb') as output, old.open('rb') as source:
                shutil.copyfileobj(source, output)
            print(f'  Preserved old file and copied completed captions to: {dest}', flush=True)
        except FileExistsError:
            pass
    if dest.is_file():
        update(db, item, 'completed', dest=dest)
        return True
    return False


def update(db, item, state, dest=None, captions=None, error=None, attempt=False):
    db.execute('''INSERT INTO jobs(item_id,title,media_path,state,srt_path,captions,attempts,error,updated_at)
         VALUES (?,?,?,?,?,?,?,?,?)
         ON CONFLICT(item_id) DO UPDATE SET
            title=excluded.title, media_path=excluded.media_path,
            state=excluded.state, srt_path=excluded.srt_path,
            captions=excluded.captions,
            attempts=jobs.attempts+?, error=excluded.error,
            updated_at=excluded.updated_at''',
        (item['Id'], item.get('Name') or 'Untitled', item.get('Path'), state,
         str(dest) if dest else None, captions, int(attempt), error, stamp(), int(attempt)))
    db.commit()


def show_status(db):
    print(f'Queue database: {DB}')
    for row in db.execute('SELECT state,COUNT(*) AS n FROM jobs GROUP BY state ORDER BY state'):
        print(f'  {row["state"]}: {row["n"]}')
    total = db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
    print(f'  tracked: {total}')
    failed = db.execute("SELECT title,error FROM jobs WHERE state='failed' ORDER BY updated_at DESC LIMIT 5")
    for row in failed:
        print(f'  FAILED {row["title"]}: {(row["error"] or "")[:160]}')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['scan', 'run', 'status'])
    limit = p.add_mutually_exclusive_group()
    limit.add_argument('--limit', type=int, default=None, help='Max videos to process this run (default: 3)')
    limit.add_argument('--all', action='store_true', help='Process all eligible videos; resumable')
    p.add_argument('--retry-failed', action='store_true', help='Reattempt failed videos')
    p.add_argument('--device', choices=['cuda', 'cpu'], default='cuda')
    p.add_argument('--chunk-seconds', type=int, default=30)
    p.add_argument('--overlap', type=float, default=3)
    args = p.parse_args()
    if args.limit is not None and args.limit < 1:
        p.error('--limit must be >= 1')
    if args.chunk_seconds < 20 or not 0 <= args.overlap <= 10:
        p.error('Invalid chunk/overlap')
    OUT.mkdir(parents=True, exist_ok=True)
    # Prevent two independent GPU queue workers from running concurrently.
    lock_file = (OUT / '.queue.lock').open('a+')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('A subtitle queue instance is already running.')
    with database() as db:
        if args.action == 'status':
            show_status(db)
            return
        if not batch.TOKEN:
            p.error('Missing Jellyfin API token in .env')
        print('Scanning Jellyfin inventory...', flush=True)
        items = list(batch.inventory(batch.user_id()))
        eligible = [i for i in items if not batch.english_text_present(i)]
        print(f'{len(items)} videos; {len(eligible)} potentially missing English text subtitles.', flush=True)
        if args.action == 'scan':
            complete = 0
            failed = 0
            for item in eligible:
                row = db.execute('SELECT state FROM jobs WHERE item_id=?', (item['Id'],)).fetchone()
                complete += bool(filename(item).is_file() or (row and row['state']=='completed'))
                failed += bool(row and row['state']=='failed')
            print(f'Local finished/tracked: {complete}; failed: {failed}; remaining estimate: {len(eligible)-complete}')
            print('Read-only Jellyfin scan; generated files unchanged.')
            return
        print('V7 mode: publishes only new fully-finalized local SRTs; media remains untouched.', flush=True)
        pending = []
        for item in eligible:
            dest = filename(item)
            row = db.execute('SELECT state,srt_path FROM jobs WHERE item_id=?', (item['Id'],)).fetchone()
            if migrate_completed(db, item, dest, row):
                continue
            if dest.is_file():
                if not row or row['state'] != 'completed':
                    update(db, item, 'completed', dest=dest)
                continue
            if row and row['state'] == 'failed' and not args.retry_failed:
                continue
            if row and row['state'] == 'no_speech' and not args.retry_failed:
                continue
            pending.append((item, dest))
        max_items = len(pending) if args.all else (args.limit or 3)
        pending = pending[:max_items]
        print(f'Pending selected for this run: {len(pending)}', flush=True)
        if not pending:
            show_status(db)
            return
        from faster_whisper import WhisperModel
        compute = 'float16' if args.device == 'cuda' else 'int8'
        print('Loading transcription model...', flush=True)
        english = WhisperModel('mobiuslabsgmbh/faster-whisper-large-v3-turbo', device=args.device, compute_type=compute)
        translation = None
        def translator():
            nonlocal translation
            if translation is None:
                print('Loading multilingual large-v3 for speech-to-English...', flush=True)
                translation = WhisperModel('large-v3', device=args.device, compute_type=compute)
            return translation
        for idx, (item, dest) in enumerate(pending, 1):
            print(f'[{idx}/{len(pending)}] {item.get("Type")}: {item.get("Name")}', flush=True)
            # Database is diagnostic only; all file creation remains guarded.
            try:
                if dest.exists():
                    print(f'  SKIP existing file: {dest}', flush=True)
                    update(db, item, 'completed', dest=dest)
                    continue
                update(db, item, 'running', attempt=True)
                # Generate into an isolated scratch directory. The existing destination
                # can NEVER be replaced: installation uses exclusive creation.
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='subtitle-queue-', dir=OUT) as scratch:
                    staged = Path(scratch) / dest.name
                    word_json = Path(scratch) / 'timed-words.json'
                    captions = batch.convert(item, english, translator, args.chunk_seconds,
                                             args.overlap, staged, word_output=word_json)
                    if captions > 0 and staged.is_file():
                        # V7 uses original timed words from this SAME transcription:
                        # no duplicate GPU Whisper pass. Pyannote still runs separately.
                        print('  Finalizing with V7 before publication...', flush=True)
                        subprocess.run([
                            sys.executable, '-u', str(ROOT / 'speaker_auto_finalize.py'),
                            '--srt', str(staged), '--words', str(word_json),
                            '--media', item.get('Path') or '', '--item-id', item['Id']
                        ], cwd=ROOT, check=True)
                        try:
                            with dest.open('xb') as output, staged.open('rb') as source:
                                shutil.copyfileobj(source, output)
                        except FileExistsError:
                            print(f'  SKIP existing file appeared: {dest}', flush=True)
                        except Exception:
                            # The destination was newly created in this attempt, not an
                            # existing user file. Keep it for inspection rather than delete.
                            raise
                if dest.is_file() and captions > 0:
                    update(db, item, 'completed', dest=dest, captions=captions)
                    print(f'  COMPLETED: {captions} captions in {dest}', flush=True)
                elif not dest.exists():
                    update(db, item, 'no_speech', captions=0)
                    print('  NO SPEECH: logged; will not retry unless requested', flush=True)
                else:
                    update(db, item, 'failed', error='Unexpected generated file state')
            except KeyboardInterrupt:
                update(db, item, 'interrupted', error='User interrupted')
                print('Interrupted; queue state saved. Run same command to resume.', flush=True)
                return
            except Exception as ex:
                update(db, item, 'failed', error=f'{type(ex).__name__}: {ex}')
                print(f'  FAILED: {type(ex).__name__}: {ex}', file=sys.stderr, flush=True)
        show_status(db)


if __name__ == '__main__':
    main()
