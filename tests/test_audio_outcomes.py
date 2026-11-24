import json
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch, Mock
from audio_outcomes import NoAudio, SilentAudio, NoSpeakerTurns, NO_SPEAKER_EXIT, RETRY_STATES, check_pcm_audio
import jellyfin_subtitle_batch as batch
import speaker_auto_finalize as finalizer
import jellyfin_subtitle_queue as queue

class AudioTests(unittest.TestCase):
    def test_pcm(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'audio.wav'
            for samples, expected in [(b'', NoAudio), (b'\0\0'*70000, SilentAudio), (b'\0\0'*70000+b'\1\0', None)]:
                with wave.open(str(p), 'wb') as w:
                    w.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                    w.writeframes(samples)
                if expected:
                    with self.assertRaises(expected):
                        check_pcm_audio(p)
                else:
                    check_pcm_audio(p)

    def test_probe(self):
        with patch.object(batch.subprocess, 'run') as run:
            run.return_value.stdout = '{"streams":[]}'
            with self.assertRaises(NoAudio):
                batch.probe_audio('source', 'auth')
            run.return_value.stdout = '{"streams":[{"index":1}]}'
            batch.probe_audio('source', 'auth')
            run.side_effect = subprocess.CalledProcessError(1, ['ffprobe'])
            with self.assertRaises(subprocess.CalledProcessError):
                batch.probe_audio('source', 'auth')

    def test_silent_skips_model(self):
        def extract(cmd, **kwargs):
            with wave.open(cmd[-1], 'wb') as w:
                w.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                w.writeframes(b'\0\0'*16000)
        factory = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'out.srt'
            with patch.object(batch, 'probe_audio'), patch.object(batch.subprocess, 'run', side_effect=extract):
                with self.assertRaises(SilentAudio):
                    batch.convert({'Id':'a'}, None, Mock(), 30, 3, dest, english_factory=factory)
            factory.assert_not_called()
            self.assertFalse(dest.exists())

    def test_finalizer_cli_exit(self):
        args = ['prog','--srt','a','--words','b','--media','c','--item-id','d']
        with patch('sys.argv', args), patch.object(finalizer, 'run', side_effect=NoSpeakerTurns('empty')):
            with self.assertRaises(SystemExit) as exc:
                finalizer.main()
            self.assertEqual(exc.exception.code, NO_SPEAKER_EXIT)
        with patch('sys.argv', args), patch.object(finalizer, 'run', side_effect=RuntimeError('bad')):
            with self.assertRaises(RuntimeError):
                finalizer.main()

    def test_queue_outcomes_and_retry(self):
        item = {'Id':'abc','Name':'Test','Path':'/media/test.mp4','Type':'Movie'}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            dbpath = out / 'jobs.sqlite3'
            with patch.object(queue, 'OUT', out), patch.object(queue, 'DB', dbpath), patch.object(batch, 'TOKEN', 'fake'), patch.object(batch, 'inventory', return_value=[item]), patch.object(batch, 'user_id', return_value='user'), patch.object(batch, 'convert', side_effect=SilentAudio('silent')) as convert:
                with patch('sys.argv',['queue','run','--limit','1']):
                    queue.main()
                    queue.main()
                self.assertEqual(convert.call_count, 1)
                with queue.database() as db:
                    self.assertEqual(db.execute('SELECT state FROM jobs').fetchone()[0], 'silent_audio')
                with patch('sys.argv',['queue','run','--retry-failed','--limit','1']):
                    queue.main()
                self.assertEqual(convert.call_count, 2)
                self.assertFalse(queue.filename(item).exists())

if __name__ == '__main__':
    unittest.main()
