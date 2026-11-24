#!/usr/bin/env python3
"""Non-destructive Ted/other completed-job word-aligned speaker preview.

A. python speaker_word_align.py transcribe --match 'Ted 2024'   # in .venv (CUDA12)
B. python speaker_word_align.py preview --match 'Ted 2024'      # either venv; CPU

Uses saved ORIGINAL SRT and cached pyannote turns from speaker_finalize.py.
Re-transcribes media only to obtain actual word timestamps, retaining the
original subtitle text. Does not modify source SRT, Jellyfin, or SQLite.
"""
from __future__ import annotations
import argparse
from difflib import SequenceMatcher
import json
import re
import sqlite3
import subprocess
from pathlib import Path

import speaker_finalize as base

WORD = re.compile(r"\S+")
CLEAN = re.compile(r"[^\w]+", re.UNICODE)
END = re.compile(r"[.!?…][\"'”’)]*$")


def normalized(word):
    return CLEAN.sub('', word.casefold())


def select(match):
    with sqlite3.connect(f'file:{base.DB.resolve()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT item_id,title,media_path,srt_path FROM jobs WHERE state='completed'").fetchall()
    found = [dict(row) for row in rows if match.casefold() in (row['title'] or '').casefold()
             or match.casefold() in (row['media_path'] or '').casefold()]
    if len(found) != 1:
        raise SystemExit(f'Expected one completed job for {match!r}; found {len(found)}')
    row = found[0]
    folder = base.ROOT / row['item_id']
    original = folder / 'original.en.srt'
    speakers = folder / 'speakers.json'
    manifest = folder / 'manifest.json'
    for p in (original, speakers, manifest):
        if not p.exists(): raise SystemExit(f'Missing previously saved file: {p}')
    metadata = json.loads(manifest.read_text())
    if (base.sha(original.read_bytes()) != metadata.get('original_sha256')
            or row['media_path'] != metadata.get('media_path')):
        raise SystemExit('Original SRT or metadata mismatch; no files changed.')
    video, _ = base.media_path(row['media_path'])
    return row, folder, original, speakers, video


def audio_length(audio):
    return float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
        'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(audio)], text=True).strip())


def transcribe(row, folder, original, speaker_file, video):
    from faster_whisper import WhisperModel
    audio = folder/'speaker-v4-audio.wav'
    word_path = folder/'speaker-v4-words.json'
    if word_path.exists():
        raise SystemExit(f'Already transcribed: {word_path} (use preview).')
    try:
        if not audio.exists():
            print('Extracting full audio to local workspace (media read-only)...', flush=True)
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
                '-i', str(video), '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(audio)], check=True)
        duration = audio_length(audio)
        model = WhisperModel('mobiuslabsgmbh/faster-whisper-large-v3-turbo',
                             device='cuda', compute_type='float16')
        temp = folder/'speaker-v4-chunk.wav'
        if temp.exists(): raise SystemExit(f'Previous temporary chunk exists: {temp}')
        failed = []

        def read_chunk(start, length, depth=0):
            if temp.exists(): raise RuntimeError(f'Unexpected file {temp}')
            try:
                subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
                                '-ss', f'{start:.3f}', '-t', f'{length:.3f}', '-i', str(audio),
                                '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(temp)], check=True)
                segments, _ = model.transcribe(str(temp), language='en', task='transcribe',
                    word_timestamps=True, vad_filter=False, condition_on_previous_text=False,
                    beam_size=5)
                found = []
                for segment in segments:
                    for word in segment.words or []:
                        if word.start is not None and word.end is not None and word.word.strip():
                            found.append({'start':start+float(word.start),
                                          'end':start+float(word.end), 'text':word.word})
                return found
            except IndexError as exc:
                if 'boolean index did not match' not in str(exc): raise
                print(f'Word alignment retry: {start:.1f}-{start+length:.1f}s', flush=True)
            finally:
                temp.unlink(missing_ok=True)
            if depth<3 and length>4.0:
                half=length/2
                return read_chunk(start,half,depth+1)+read_chunk(start+half,length-half,depth+1)
            failed.append([round(start,3),round(start+length,3)])
            return []

        all_words=[]
        print('GPU word-timestamp transcription (original SRT text will be retained)...', flush=True)
        for second in range(0,int(duration)+1,30):
            if second>=duration: break
            all_words.extend(read_chunk(float(second),min(30.,duration-second)))
            if (second//30+1)%10==0:
                print(f'  {min(second+30,duration):.0f}/{duration:.0f}s, {len(all_words)} words',flush=True)
        if not all_words:raise SystemExit('No transcribed words; no result saved.')
        # File x preserves previous results in case of accidental rerun.
        with word_path.open('x',encoding='utf8') as f:
            json.dump({'words':all_words,'failed_ranges':failed,
                       'audio_duration':duration},f,ensure_ascii=False)
        print(f'Created {word_path}; words={len(all_words)}; failed_ranges={len(failed)}')
        if failed: print('WARNING: Some intervals lack real word timings; previews will avoid unanchored splits.')
    finally:
        audio.unlink(missing_ok=True)



def align(cues, whisper):
    """Anchor SRT tokens to Whisper words using order-preserving exact matches.

    SRT words don't inherit Whisper's potentially inaccurate transcription.
    Only exact lexical matches count as anchors; approximate matches are not
    used as a basis for splitting.
    """
    flat=[]
    for cue_idx,(start,end,text) in enumerate(cues):
        for token in WORD.finditer(text):
            flat.append({'cue':cue_idx,'token':token,
                         'word':normalized(token.group()),'start':start,'end':end})
    srt_tokens=[t['word'] for t in flat]
    asr_tokens=[normalized(t['text']) for t in whisper]
    matcher=SequenceMatcher(None,srt_tokens,asr_tokens,autojunk=False)
    anchor={}
    for op,a0,a1,b0,b1 in matcher.get_opcodes():
        if op=='equal':
            for i,j in zip(range(a0,a1),range(b0,b1)):
                if srt_tokens[i] and whisper[j]['end']>=whisper[j]['start']:
                    # Reject plausible duplicate-match ambiguity far outside SRT cue.
                    if whisper[j]['end'] >= flat[i]['start']-1.5 and whisper[j]['start'] <= flat[i]['end']+1.5:
                        anchor[i] = {'start':whisper[j]['start'], 'end':whisper[j]['end']}
    return flat,anchor


def transitions(turns,start,end):
    # Per-speaker adjacent turns, eligible boundaries when genuinely within cue.
    ts=sorted(turns,key=lambda x:x['start'])
    result=[]
    for left,right in zip(ts,ts[1:]):
        if left['speaker']==right['speaker']:continue
        center=(left['end']+right['start'])/2
        if not start+.12<center<end-.12:continue
        before=max(0.,min(left['end'],end)-max(left['start'],start))
        after=max(0.,min(right['end'],end)-max(right['start'],start))
        if before < .16 or after < .16:continue
        result.append(center)
    return result


def preview(row,folder,original,speaker_file,video):
    words_file=folder/'speaker-v4-words.json'
    if not words_file.exists():raise SystemExit('First run: python speaker_word_align.py transcribe --match "Ted 2024"')
    data=json.loads(words_file.read_text())
    original_cues=base.parse_srt(original.read_bytes())
    # The cached speaker turns are stored as a list, not a dict.
    turns=json.loads(speaker_file.read_text())
    if isinstance(turns,dict): turns=turns['turns']
    flat, anchors=align(original_cues,data['words'])
    by_cue={}
    for idx, token in enumerate(flat):by_cue.setdefault(token['cue'],[]).append(idx)
    out=[];split_count=0
    for ci,(start,end,text) in enumerate(original_cues):
        indices=by_cue.get(ci,[])
        possible=transitions(turns,start,end)
        if not possible or len(indices)<2:
            out.append((start,end,text));continue
        tokens=[flat[i]['token'] for i in indices]
        choices=[]
        for pos in range(1,len(indices)):
            li,ri=indices[pos-1],indices[pos]
            if li not in anchors or ri not in anchors:continue
            prior=anchors[li];following=anchors[ri]
            if prior['start']>following['start'] or prior['end']>following['end']+.3:continue
            t=(prior['end']+following['start'])/2
            if not start+.30<t<end-.30:continue
            if abs(following['start']-prior['end'])>1.1:continue
            delta=min(abs(t-x) for x in possible)
            if delta>.46:continue
            punctuation=bool(END.search(tokens[pos-1].group()))
            # Reliable audio timing allows non-punctuation boundaries, but
            # prefers punctuation when there are multiple near one turn.
            choices.append((delta-(.06 if punctuation else 0),pos,t))
        picks=[]
        for score,pos,t in sorted(choices):
            if any(abs(t-other[1])<.70 or abs(pos-other[0])<2 for other in picks):continue
            picks.append((pos,t))
        picks.sort()
        if not picks:
            out.append((start,end,text));continue
        edges=[(0,start)]+picks+[(len(indices),end)]
        parts=[]
        for (a,ta),(b,tb) in zip(edges,edges[1:]):
            if b<=a or tb-ta<.34:parts=[];break
            segment=text[tokens[a].start():tokens[b-1].end()].strip()
            if not segment:parts=[];break
            parts.append((ta,tb,segment))
        if not parts:
            out.append((start,end,text));continue
        out.extend(parts);split_count+=len(parts)-1
    out_file=folder/'speaker-v4-preview.en.srt'
    if out_file.exists():raise SystemExit(f'Preview already exists; preserving {out_file}')
    with out_file.open('xb') as f:f.write(base.render(out))
    matched=len(anchors)
    print(f'Title: {row["title"]}')
    print(f'Original cues: {len(original_cues)}; matched timing anchors: {matched}/{len(flat)} ({100*matched/max(1,len(flat)):.1f}%)')
    print(f'Word-aligned speaker splits: {split_count}; final cues: {len(out)}')
    print(f'Under 0.5s: {sum((b-a)<.5 for a,b,_ in out)}; one-word cues: {sum(len(t.split())==1 for _,_,t in out)}')
    print(f'PREVIEW: {out_file}')
    print('No Jellyfin subtitles, batch jobs, previous upgrades, or backups modified.')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stage',choices=['transcribe','preview'])
    ap.add_argument('--match',required=True)
    args=ap.parse_args()
    row,folder,original,speakers,video=select(args.match)
    if args.stage=='transcribe':transcribe(row,folder,original,speakers,video)
    else:preview(row,folder,original,speakers,video)

if __name__=='__main__': main()
