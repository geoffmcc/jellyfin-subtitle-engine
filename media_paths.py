"""Explicit, safe mapping from Jellyfin's media paths to a local mount."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from config import read_env

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".ts", ".webm"}


def media_roots() -> tuple[PurePosixPath, Path]:
    settings = read_env()
    server = os.environ.get("MEDIA_SERVER_ROOT") or settings.get("MEDIA_SERVER_ROOT")
    mount = os.environ.get("MEDIA_MOUNT_ROOT") or settings.get("MEDIA_MOUNT_ROOT")
    if not server or not mount:
        raise RuntimeError(
            "Set MEDIA_SERVER_ROOT and MEDIA_MOUNT_ROOT in .env or the environment"
        )
    prefix = PurePosixPath(server)
    local = Path(mount)
    if not prefix.is_absolute() or not local.is_absolute() or ".." in prefix.parts:
        raise ValueError("Media roots must be absolute paths without parent traversal")
    if prefix == PurePosixPath("/"):
        raise ValueError("MEDIA_SERVER_ROOT must not be /")
    if not local.is_dir():
        raise FileNotFoundError(f"Media mount does not exist: {local}")
    return prefix, local


def media_destination(server_path: str) -> tuple[Path, Path]:
    """Map one video; reject paths escaping the configured mount, including symlinks."""
    prefix, mount = media_roots()
    p = PurePosixPath(server_path)
    if not p.is_absolute() or ".." in p.parts or not p.is_relative_to(prefix):
        raise ValueError("Media path is outside MEDIA_SERVER_ROOT")
    relative = p.relative_to(prefix)
    if not relative.parts or p.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("Not a supported video path")
    video = mount.joinpath(*relative.parts)
    root = mount.resolve(strict=True)
    # Resolve even for a missing video, so a symlinked parent cannot escape.
    if not video.resolve(strict=False).is_relative_to(root):
        raise ValueError("Media path escapes MEDIA_MOUNT_ROOT")
    return video, video.with_name(video.stem + ".en.srt")
