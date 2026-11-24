#!/usr/bin/env python3
"""Local, conservative speaker separation for existing English SRTs.

Run under .venv-diarization with LD_LIBRARY_PATH unset. No speech is
retranscribed: original text is retained. Uses pyannote to split an existing
subtitle cue when two distinct speaker turns occur inside it.

  python speaker_finalize.py upgrade --limit 1
  python speaker_finalize.py upgrade --all
  python speaker_finalize.py finalize --srt /path/to/new.srt --media /WD-Black/...

upgrade performs SHA256-guarded installation of improved SRTs only when the
existing installed file exactly matches the original local file. It saves
backups outside the media library and can resume after interruption.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
DB = HERE / 'subtitles/batch/subtitle_jobs.sqlite3'
ROOT = HERE / 'subtitles/speaker-upgrades'
MOUNT = Path('/mnt/x')
PREFIX = PurePosixPath('/WD-Black')
TIMESTAMP = re.compile(r'^(\d\d):(\d\d):(\d\d),(\d{3})$')
TIME_LINE = re.compile(r'^(\S+)\s+-->\s+(\S+)(?:\s+.*)?$')
TOKEN = re.compile(r'\S+')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def secs(s):
    m=TIMESTAMP.fullmatch(s)
    if not m: raise ValueError(f'Invalid SRT timestamp: {s}')
    h,minute,second,ms=map(int,m.groups())
    return h*3600+minute*60+second+ms/1000


def stamp(t):
    ms=max(0,round(t*1000)); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def parse_srt(data):
    content=data.decode('utf-8-sig')
    blocks=re.split(r'\n\s*\n',content.replace('\r\n','\n').replace('\r','\n').strip())
    cues=[]
    for block in blocks:
        lines=block.strip().split('\n')
        if not lines:continue
        i=1 if lines[0].strip().isdigit() else 0
        if len(lines)<=i+1:raise ValueError('Unparseable SRT block')
        match=TIME_LINE.match(lines[i].strip())
        if not match:raise ValueError(f'Unparseable SRT time: {lines[i]}')
        start,end=secs(match[1]),secs(match[2])
        if end<=start:raise ValueError('Invalid SRT cue duration')
        txt='\n'.join(lines[i+1:]).strip()
        if not txt:raise ValueError('Empty subtitle cue')
        cues.append((start,end,txt))
    if not cues:raise ValueError('No SRT cues')
    return cues


def render(cues):
    return ''.join(f'{i}\n{stamp(a)} --> {stamp(b)}\n{text}\n\n' for i,(a,b,text) in enumerate(cues,1)).encode('utf-8')


def media_path(server_path):
    p=PurePosixPath(server_path)
    if not p.is_absolute() or '..' in p.parts or not p.is_relative_to(PREFIX):
        raise ValueError('Only media paths under /WD-Black permitted')
    relative=p.relative_to(PREFIX)
    if not relative.parts or p.suffix.lower() not in {'.mkv','.mp4','.avi','.mov','.m4v','.ts','.webm'}:
        raise ValueError('Not a supported media path')
    video=MOUNT.joinpath(*relative.parts)
    if not video.is_file():raise FileNotFoundError(video)
    return video, video.with_name(video.stem+'.en.srt')


def get_speakers(video,folder):
    speakers=folder/'speakers.json'
    if speakers.exists():
        return json.loads(speakers.read_text(encoding='utf-8'))
    wav=folder/'audio.wav'
    folder.mkdir(parents=True,exist_ok=True)
    if not wav.exists():
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-n',
                        '-i',str(video),'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(wav)],check=True)
    import torch
    from pyannote.audio import Pipeline
    token=os.environ.get('HF_TOKEN')
    if not token: raise RuntimeError('HF_TOKEN is missing; export it in this shell')
    pipeline=Pipeline.from_pretrained('pyannote/speaker-diarization-community-1',token=token)
    pipeline.to(torch.device('cuda'))
    result=pipeline(str(wav))
    turns=[{'start':float(t.start),'end':float(t.end),'speaker':str(s)}
           for t,_,s in result.exclusive_speaker_diarization.itertracks(yield_label=True)
           if t.end-t.start>=.3]
    if not turns: raise ValueError('No speech turns detected')
    with speakers.open('x',encoding='utf-8') as f:json.dump(turns,f)
    # This temporary audio exists only in our private work folder.
    wav.unlink(missing_ok=True)
    return turns


def break_cues(cues,turns):
    """Preserve original text; split at high-confidence multi-speaker boundaries.

    Word timings within a cue are estimated from character weights. Therefore
    split only when turns have reasonable duration on either side of boundary.
    """
    turns=sorted(turns,key=lambda t:t['start'])
    output=[]; splits=0
    for start,end,text in cues:
        tokens=list(TOKEN.finditer(text))
        if len(tokens)<6 or end-start<1.5:
            output.append((start,end,text));continue
        boundaries=[]
        for previous,following in zip(turns,turns[1:]):
            if previous['speaker']==following['speaker']:continue
            center=(previous['end']+following['start'])/2
            if not (start+.5<center<end-.5):continue
            if previous['end']<start+.35 or following['start']>end-.35:continue
            # Both turns must materially overlap the cue.
            if min(previous['end'],end)-max(previous['start'],start)<.35:continue
            if min(following['end'],end)-max(following['start'],start)<.35:continue
            boundaries.append(center)
        if not boundaries:
            output.append((start,end,text));continue
        # Split on nearest token boundary, with punctuation preference.
        positions=[]
        weight=[max(1,len(m.group())) for m in tokens]
        totals=sum(weight)
        for i in range(2,len(tokens)-1):
            ratio=sum(weight[:i])/totals
            if ratio<.18 or ratio>.82:continue
            left=tokens[i-1].group()
            bonus=.20 if re.search(r'[.!?]["\']?$',left) else 0
            positions.append((i,start+(end-start)*ratio,bonus))
        candidates=[]
        for b in boundaries:
            if not positions:break
            i,t,bonus=min(positions,key=lambda x:abs(x[1]-b)-x[2])
            if abs(t-b)>.85:continue
            candidates.append((i,t))
        # Multiple splits per cue require dependable word alignment; choose one.
        if not candidates:
            output.append((start,end,text));continue
        idx,t=min(candidates,key=lambda p:abs(p[1]-(start+end)/2))
        left=text[:tokens[idx].start()].strip()
        right=text[tokens[idx].start():].strip()
        if not left or not right:
            output.append((start,end,text));continue
        output.append((start,t,left))
        output.append((t,end,right))
        splits+=1
    return output,splits


def atomic_write(path,data):
    fd,tmp=tempfile.mkstemp(prefix='.speaker-stage-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def run_one(item_id,server_path,src,installed,install):
    src=Path(src).resolve(strict=True)
    allowed=(HERE/'subtitles/batch').resolve()
    if not src.is_relative_to(allowed):raise ValueError('Source outside generator output tree')
    video,dest=media_path(server_path)
    if installed and dest!=installed:raise ValueError('Mismatch in installation path')
    folder=ROOT/item_id
    folder.mkdir(parents=True,exist_ok=True)
    backup=folder/'original.en.srt'
    improved=folder/'speaker.en.srt'
    manifest=folder/'manifest.json'
    if not backup.exists():
        # Backup original before touching anything.
        with backup.open('xb') as f:f.write(src.read_bytes())
    original=backup.read_bytes()
    old_hash=sha(original)
    current=src.read_bytes()
    if manifest.exists():
        metadata=json.loads(manifest.read_text())
        if metadata['original_sha256']!=old_hash or metadata['media_path']!=server_path:
            raise ValueError('Upgrade manifest mismatch')
    if not improved.exists():
        base=parse_srt(original)
        turns=get_speakers(video,folder)
        output,num_splits=break_cues(base,turns)
        if num_splits==0:
            print(f'  NO SPLITS for {video.name}; preserving original')
            return False
        upgraded=render(output)
        if sha(upgraded)==old_hash:raise ValueError('No change after supposed splits')
        with improved.open('xb') as f:f.write(upgraded)
        manifest.write_text(json.dumps({'media_path':server_path,'original_sha256':old_hash,
                                        'improved_sha256':sha(upgraded),'splits':num_splits},indent=2)+'\n')
    upgraded=improved.read_bytes()
    new_hash=sha(upgraded)
    if current!=upgraded:
        if current!=original:raise ValueError('Local SRT changed independently; refusing replacement')
        atomic_write(src,upgraded)
        print(f'  LOCAL FINALIZED: {src}')
    if install:
        if not dest.exists():
            # Installer can safely create final version later.
            print(f'  NOT INSTALLED YET; installer will copy the final .en.srt')
        else:
            remote=dest.read_bytes()
            if sha(remote)==new_hash:
                print('  Already upgraded in media library')
            elif sha(remote)==old_hash and remote==original:
                atomic_write(dest,upgraded)
                print(f'  MEDIA UPDATED after verified backup: {dest}')
            else:
                print(f'  SKIPPED MEDIA FILE: differs from our original; untouched: {dest}')
    return True


def upgrade(args):
    if not DB.exists():raise SystemExit(f'Queue DB missing: {DB}')
    if not (MOUNT/'Media/Video').is_dir():raise SystemExit('Media share unavailable; check /mnt/x')
    uri=f'file:{DB.resolve()}?mode=ro'
    with sqlite3.connect(uri,uri=True) as c:
        c.row_factory=sqlite3.Row
        rows=c.execute("SELECT item_id,title,media_path,srt_path FROM jobs WHERE state='completed' ORDER BY updated_at,item_id").fetchall()
    if args.match:
        rows=[row for row in rows if args.match.casefold() in (row['title'] or '').casefold() or args.match.casefold() in (row['media_path'] or '').casefold()]
    if not args.all:rows=rows[:args.limit]
    print(f'Processing {len(rows)} completed jobs. Backups under {ROOT}.',flush=True)
    succeeded=0;failed=0
    for index,row in enumerate(rows,1):
        print(f'[{index}/{len(rows)}] {row["title"]}',flush=True)
        try:
            if run_one(row['item_id'],row['media_path'],row['srt_path'],None,True):succeeded+=1
        except KeyboardInterrupt:raise
        except Exception as exc:
            failed+=1; print(f'  ERROR (left existing media unchanged if not verified): {exc}',flush=True)
    print(f'Finished: changed_or_previously_changed={succeeded}; errors={failed}',flush=True)


def finalize(args):
    if not args.srt or not args.media:raise SystemExit('--srt and --media required')
    src=Path(args.srt)
    media=args.media
    # Finalizing a not-yet-installed file: backed up in project tree first.
    key=hashlib.sha256((media+'\0'+str(src.resolve())).encode()).hexdigest()[:32]
    run_one(key,media,src,None,False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['upgrade','finalize'])
    p.add_argument('--all',action='store_true')
    p.add_argument('--match',help='Process only jobs with matching title/media path')
    p.add_argument('--limit',type=int,default=1)
    p.add_argument('--srt')
    p.add_argument('--media')
    args=p.parse_args()
    if args.mode=='upgrade':upgrade(args)
    else:finalize(args)
