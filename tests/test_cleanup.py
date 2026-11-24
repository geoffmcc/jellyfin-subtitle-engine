import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
import speaker_finalize
import jellyfin_subtitle_cleanup as cleanup

class CleanupTests(unittest.TestCase):
    def test_preview_apply_and_protected_files(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(StringIO()):
            root = Path(tmp)
            folder = root / ("a" * 32)
            folder.mkdir()
            audio = folder / "speaker-v4-audio.wav"
            audio.write_bytes(b"123")
            protected = folder / "original.en.srt"
            protected.write_text("keep")
            unknown = folder / "other.wav"
            unknown.write_bytes(b"keep")
            link = folder / "audio.wav"
            link.symlink_to(protected)
            alias = root / ("b" * 32)
            alias.symlink_to(folder, target_is_directory=True)
            self.assertEqual(cleanup.cleanup(root), (1, 3))
            self.assertTrue(audio.exists())
            self.assertEqual(cleanup.cleanup(root, True), (1, 3))
            self.assertFalse(audio.exists())
            self.assertTrue(link.is_symlink())
            self.assertEqual(protected.read_text(), "keep")
            self.assertTrue(unknown.exists())

    def test_diarization_extraction_failure_removes_partial_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            wav = folder / "audio.wav"
            def fail(*args, **kwargs):
                wav.write_bytes(b"partial")
                raise RuntimeError("ffmpeg failed")
            with patch.object(speaker_finalize.subprocess, "run", side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, "ffmpeg failed"):
                    speaker_finalize.get_speakers(Path("video.mp4"), folder)
            self.assertFalse(wav.exists())

    def test_diarization_interrupt_removes_partial_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            wav = folder / "audio.wav"
            def interrupt(*args, **kwargs):
                wav.write_bytes(b"partial")
                raise KeyboardInterrupt
            with patch.object(speaker_finalize.subprocess, "run", side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    speaker_finalize.get_speakers(Path("video.mp4"), folder)
            self.assertFalse(wav.exists())

if __name__ == "__main__":
    unittest.main()
