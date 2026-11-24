#!/usr/bin/env python3
"""Preview or remove explicitly named disposable audio from the local workspace.

Stop all queue, installer and standalone speaker processes before applying.
The queue lock prevents concurrent queue operation, but legacy speaker tools
do not share that lock.
"""
import argparse
import fcntl
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
NAMES = {"audio.wav", "speaker-v4-audio.wav", "speaker-v4-chunk.wav"}
FOLDER = re.compile(r"(?:auto-v7-)?[0-9a-f]{32}")

def candidates(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        return
    for folder in sorted(root.iterdir()):
        if folder.is_symlink() or not folder.is_dir() or not FOLDER.fullmatch(folder.name):
            continue
        for name in sorted(NAMES):
            p = folder / name
            if not p.is_symlink() and p.is_file():
                yield p

def cleanup(root, apply=False):
    count = size = 0
    for p in candidates(root):
        n = p.stat().st_size
        if apply:
            p.unlink()
        print(f"{'REMOVED' if apply else 'WOULD REMOVE'}: {p} ({n} bytes)")
        count += 1
        size += n
    return count, size

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Remove listed audio")
    parser.add_argument("--offline", action="store_true", help="Confirm all other processing tools are stopped")
    args = parser.parse_args()
    if args.apply and not args.offline:
        parser.error("--apply requires --offline; stop all processing tools first")
    batch = ROOT / "subtitles" / "batch"
    if (ROOT / "subtitles").is_symlink() or batch.is_symlink():
        parser.error("Refusing symlinked workspace")
    batch.mkdir(parents=True, exist_ok=True)
    lock = batch / ".queue.lock"
    if lock.is_symlink():
        parser.error("Refusing symlinked lock")
    with lock.open("a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Queue is running; stop it before cleanup")
        count, size = cleanup(ROOT / "subtitles" / "speaker-upgrades", args.apply)
    print(f"{'Removed' if args.apply else 'Recoverable'}: {count} files, {size / 2**30:.2f} GiB")

if __name__ == "__main__":
    main()
