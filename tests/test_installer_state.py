"""Tests for install bookkeeping; no media, GPU, or Jellyfin required."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jellyfin_subtitle_install as installer


class InstallerStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'install-state.sqlite3'
        self.mock = patch.object(installer, 'STATE_DB', self.path)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def row(self, updated='2026-11-24T00:00:00Z', path='/mnt/captions/a.en.srt'):
        return {'item_id': 'item-1', 'updated_at': updated,
                'media_path': '/videos/a.mkv', 'srt_path': path}

    def test_mark_and_skip_same_completed_job(self):
        with installer.open_state(True) as state:
            row = self.row()
            self.assertFalse(installer.already_handled(state, row))
            installer.mark_handled(state, row)
            self.assertTrue(installer.already_handled(state, row))
        with installer.open_state(False) as state:
            self.assertTrue(installer.already_handled(state, self.row()))

    def test_new_job_version_is_rechecked(self):
        with installer.open_state(True) as state:
            installer.mark_handled(state, self.row())
            self.assertFalse(installer.already_handled(
                state, self.row(updated='2026-11-24T01:00:00Z')))
            self.assertFalse(installer.already_handled(
                state, self.row(path='/mnt/captions/new.en.srt')))

    def test_preview_does_not_create_state_db(self):
        with installer.open_state(False) as state:
            self.assertFalse(installer.already_handled(state, self.row()))
        self.assertFalse(self.path.exists())

    def test_mark_replaces_old_version(self):
        with installer.open_state(True) as state:
            installer.mark_handled(state, self.row())
            later = self.row(updated='2026-11-25T00:00:00Z')
            installer.mark_handled(state, later)
            self.assertTrue(installer.already_handled(state, later))
            self.assertFalse(installer.already_handled(state, self.row()))


if __name__ == '__main__':
    unittest.main()
