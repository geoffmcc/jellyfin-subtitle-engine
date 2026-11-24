#!/usr/bin/env python3
"""Finalize ONE *new* locally staged SRT using existing V5/V6 logic.

Called by jellyfin_subtitle_queue.py after word timestamps have been generated,
but before publishing a completed job. Never writes to Jellyfin or media. Original
SRT, timed words, speaker turns and final output are stored in a private job
folder. No replacement of existing local or remote subtitles occurs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from audio_outcomes import NoSpeakerTurns, NO_SPEAKER_EXIT
import speaker_finalize as base
import speaker_sentence as v5
import speaker_cleanup as v6


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save_once(path: Path, data: bytes):
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f'Existing saved artifact differs: {path}')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as f:
            f.write(data)


def run(srt: Path, words_file: Path, media: str, item_id: str):
    # Never accept user-supplied SRT files outside our temporary generator tree.
    allowed = (base.HERE / 'subtitles/batch').resolve()
    src = srt.resolve(strict=True)
    if not src.is_relative_to(allowed):
        raise ValueError('SRT outside generated scratch workspace')
    words_path = words_file.resolve(strict=True)
    if not words_path.is_relative_to(allowed):
        raise ValueError('Word timestamps outside generator scratch workspace')
    video, installed = base.media_path(media)
    if installed.exists():
        raise ValueError('Media subtitle already exists; refusing to generate another version')
    # Namespaced to avoid collision with earlier completed-job backup IDs.
    folder = base.ROOT / ('auto-v7-' + hashlib.sha256((item_id+'\0'+media).encode()).hexdigest()[:32])
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / 'auto-v7-manifest.json'
    source_bytes = src.read_bytes()
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text('utf8'))
        if manifest.get('media_path') != media or manifest.get('item_id') != item_id:
            raise ValueError('Previous auto-v7 manifest belongs to different media')
    original_path = folder / 'original.en.srt'
    # On retry, adopt the already saved original: Whisper can produce slightly
    # different wording on a second run, but prior backup must never be overwritten.
    if original_path.exists():
        source_bytes = original_path.read_bytes()
    else:
        save_once(original_path, source_bytes)
    word_data = json.loads(words_path.read_text('utf8'))
    if word_data.get('failed_ranges'):
        raise ValueError('Word transcription contained failed ranges')
    if not word_data.get('words'):
        raise ValueError('No timed words found')
    saved_words = folder / 'speaker-v4-words.json'
    # Timestamp sequences may differ across retry. Preserve the first successful
    # pass for stable V7 final output.
    if not saved_words.exists():
        save_once(saved_words, (json.dumps(word_data,ensure_ascii=False)+'\n').encode())
    word_data = json.loads(saved_words.read_text('utf8'))
    speaker_path = folder / 'speakers.json'
    if not speaker_path.exists():
        if not os.environ.get('HF_TOKEN'):
            raise RuntimeError('HF_TOKEN missing: required to analyze new speaker turns')
        speaker_python = base.HERE / '.venv-diarization/bin/python'
        if not speaker_python.is_file():
            raise RuntimeError('Missing .venv-diarization Python')
        env = os.environ.copy()
        env.pop('LD_LIBRARY_PATH',None)
        # Both environments access the same private backup folder. pyannote
        # writes speaker turns and deletes its own intermediate audio.wav.
        code = (
            'import sys; from pathlib import Path; import speaker_finalize as s; '
            'from audio_outcomes import NoSpeakerTurns, NO_SPEAKER_EXIT\n'
            'try: s.get_speakers(Path(sys.argv[1]), Path(sys.argv[2]))\n'
            'except NoSpeakerTurns: sys.exit(NO_SPEAKER_EXIT)'
        )
        print('  Detecting speaker boundaries (pyannote GPU)...',flush=True)
        try:
            subprocess.run([str(speaker_python),'-u','-c',code,str(video),str(folder)],
                           cwd=base.HERE, env=env, check=True)
        except subprocess.CalledProcessError as exc:
            if exc.returncode == NO_SPEAKER_EXIT:
                raise NoSpeakerTurns('No speech turns detected') from exc
            raise
    turns = json.loads(speaker_path.read_text('utf8'))
    if isinstance(turns,dict):
        turns=turns.get('turns',[])
    if not turns:
        raise NoSpeakerTurns('No speech turns detected')
    original_cues=base.parse_srt(source_bytes)
    intermediate,matched,total,candidates,splits = v5.split_cues(original_cues,word_data['words'],turns)
    rate=matched/max(1,total)
    if rate < .75:
        raise ValueError(f'Word alignment only {rate:.1%}; refusing publication (minimum 75%)')
    cleaned,joins,moved = v6.cleanup(intermediate)
    canonical=lambda cues:re.sub(r'\s+','', ''.join(t for _,_,t in cues))
    if canonical(original_cues)!=canonical(cleaned):
        raise ValueError('Subtitle text integrity failed after V7 cleanup')
    if any(not (0<=a<b) for a,b,_ in cleaned):
        raise ValueError('Invalid subtitle cue duration')
    final=base.render(cleaned)
    save_once(folder/'speaker-v7-final.en.srt',final)
    stats={'item_id':item_id,'media_path':media,'source_sha256':digest(source_bytes),
           'final_sha256':digest(final),'source_cues':len(original_cues),
           'final_cues':len(cleaned),'alignment_rate':round(rate,4),
           'splits':splits,'joins':joins,'moved':moved}
    save_once(manifest_path,(json.dumps(stats,indent=2)+'\n').encode())
    # Recheck media sidecar before updating only the disposable scratch file.
    if installed.exists():raise ValueError('Media subtitle appeared during processing')
    base.atomic_write(src,final)
    print(f'  V7 FINALIZED: {len(original_cues)} -> {len(cleaned)} cues; '
          f'aligned {rate:.1%}; splits {splits}; joins {joins}',flush=True)
    return folder


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--srt',required=True)
    ap.add_argument('--words',required=True)
    ap.add_argument('--media',required=True)
    ap.add_argument('--item-id',required=True)
    a=ap.parse_args()
    try:
        run(Path(a.srt),Path(a.words),a.media,a.item_id)
    except NoSpeakerTurns as exc:
        print(f'  NO SPEAKER TURNS: {exc}', flush=True)
        raise SystemExit(NO_SPEAKER_EXIT)

if __name__=='__main__':main()
