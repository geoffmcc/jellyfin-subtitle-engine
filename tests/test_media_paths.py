"""No GPU, media library or Jellyfin server required for these tests."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from media_paths import media_destination, media_roots


class MediaPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mount = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "MEDIA_SERVER_ROOT": "/srv/videos",
            "MEDIA_MOUNT_ROOT": str(self.mount),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_media_mapping(self):
        video, sub = media_destination("/srv/videos/Shows/Episode.mkv")
        self.assertEqual(video, self.mount / "Shows" / "Episode.mkv")
        self.assertEqual(sub, self.mount / "Shows" / "Episode.en.srt")

    def test_outside_root_rejected(self):
        with self.assertRaises(ValueError):
            media_destination("/srv/private/Episode.mkv")

    def test_parent_traversal_rejected(self):
        with self.assertRaises(ValueError):
            media_destination("/srv/videos/../private/Episode.mkv")

    def test_unknown_extension_rejected(self):
        with self.assertRaises(ValueError):
            media_destination("/srv/videos/notes.txt")

    def test_relative_root_rejected(self):
        with patch.dict(os.environ, {"MEDIA_SERVER_ROOT": "relative"}):
            with self.assertRaises(ValueError):
                media_roots()

    def test_root_slash_rejected(self):
        with patch.dict(os.environ, {"MEDIA_SERVER_ROOT": "/"}):
            with self.assertRaises(ValueError):
                media_roots()

    def test_symlink_escape_rejected(self):
        outside = self.mount.parent / ("escape-" + self.mount.name)
        # No need to create the outside path; resolve(strict=False) follows symlinks.
        (self.mount / "escape").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            media_destination("/srv/videos/escape/Episode.mkv")


if __name__ == "__main__":
    unittest.main()
