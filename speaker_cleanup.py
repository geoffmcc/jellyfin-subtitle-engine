#!/usr/bin/env python3
"""Read-only readability cleanup for speaker_sentence.py preview.

This is an optional V6 preview: it never edits or installs subtitles. It joins
obviously unfinished phrases across touching cues and repairs a short stray
phrase after a full-stop ('Fuck off. Try' + 'it again ...').

Usage in Jellyfin project:
    python speaker_cleanup.py preview --match 'Ted 2024'

Standalone for inspection:
    python speaker_cleanup.py preview --input my-v5.srt --output my-v6.srt
"""
from __future__ import annotations
import argparse
import re
from pathlib import Path

BLOCK = re.compile(r'\n\s*\n')
TIMING = re.compile(r'^(\d\d):(\d\d):(\d\d),(\d\d\d)\s*-->\s*(\d\d):(\d\d):(\d\d),(\d\d\d)')
END = re.compile(r'[.!?…]["\'”’)]*$')
REPLY = re.compile(r'(?i)^(?:no|yes|nah|yeah|yep|nope|ok|okay|what|why|who|huh|hey|wait|stop|sure|right|thanks|sorry|shoot|fuck off|fuck you|shut up)[.!?…]*$')
TAIL = re.compile(r'^(.*?[.!?…]["\'”’)]?)\s+((?:[^\s]+\s*){1,3})$', re.S)

def seconds(groups):
    h,m,s,ms = (int(v) for v in groups)
    return 3600*h+60*m+s+ms/1000

def clock(t):
    n=round(max(0,t)*1000)
    h,n=divmod(n,3600000); m,n=divmod(n,60000); s,ms=divmod(n,1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'

def read_srt(path):
    raw=path.read_text(encoding='utf-8-sig').replace('\r\n','\n').strip()
    cues=[]
    for block in BLOCK.split(raw):
        lines=block.splitlines()
        if not lines:continue
        pos=1 if lines[0].strip().isdigit() else 0
        m=TIMING.match(lines[pos]) if len(lines)>pos else None
        if not m:raise ValueError(f'Malformed SRT block: {lines[:2]}')
        txt=' '.join(x.strip() for x in lines[pos+1:]).strip()
        cues.append([seconds(m.groups()[:4]),seconds(m.groups()[4:]),txt])
    return cues

def render(cues):
    return ''.join(f'{i}\n{clock(a)} --> {clock(b)}\n{text}\n\n'
                   for i,(a,b,text) in enumerate(cues,1)).encode('utf-8')

def is_partial(text):
    return not bool(END.search(text.strip()))

def can_join(a,b):
    start,end,left=a; nxt_start,nxt_end,right=b
    if nxt_start-end < -.12 or nxt_start-end > .12:return False
    if nxt_end-start > 5.6:return False
    if len(left)+len(right)>116:return False
    if not is_partial(left):return False
    if REPLY.fullmatch(left.strip()) or REPLY.fullmatch(right.strip()):return False
    # Avoid merging speech that could be an abrupt reply of a different speaker.
    if re.match(r'(?i)^(?:no[,.! ]|yes[,.! ]|yeah[,.! ]|oh[,.! ]|what[?! ]|fuck you\b|fuck off\b)',right):
        return False
    return True

def cleanup(cues):
    cues=[list(c) for c in cues]
    moved=0
    # Shift a short, obvious sentence fragment from the end of a cue into the
    # following cue. This fixes 'Fuck off. Try' / 'it again with eye contact.'
    for i in range(len(cues)-1):
        a,b=cues[i],cues[i+1]
        if b[0]-a[1] > .13 or b[0]-a[1] < -.12:continue
        m=TAIL.match(a[2])
        if not m:continue
        complete,tail=m.group(1).strip(),m.group(2).strip()
        if not complete or len(tail.split())>2 or not tail or END.search(tail):continue
        if not b[2] or not b[2][0].islower():continue
        if REPLY.fullmatch(tail):continue
        dur=a[1]-a[0]
        if dur<.65:continue
        # Time the boundary using a fraction of the existing cue's duration;
        # no words are invented. Do not create sub-0.3s fragments.
        count_left=len(complete.split()); count_tail=len(tail.split())
        cut=a[0]+dur*count_left/(count_left+count_tail)
        if cut-a[0]<.30 or a[1]-cut<.18:continue
        a[1]=cut; a[2]=complete
        b[0]=cut; b[2]=tail+' '+b[2]
        moved+=1
    # Merge unfinished phrases across adjacent cues. Only merge if the first
    # cue has no terminating punctuation, respecting clear standalone replies.
    out=[]; joins=0
    for cue in cues:
        if out and can_join(out[-1],cue):
            out[-1][1]=cue[1]
            out[-1][2]=out[-1][2].rstrip()+' '+cue[2].lstrip()
            joins+=1
        else:out.append(cue[:])
    return out,joins,moved

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=['preview'])
    ap.add_argument('--match',help='Job match, e.g. Ted 2024')
    ap.add_argument('--input',type=Path,help='Standalone V5 preview path')
    ap.add_argument('--output',type=Path,help='Standalone V6 output path')
    args=ap.parse_args()
    if args.input:
        src=args.input
        dst=args.output or src.with_name('speaker-v6-preview.en.srt')
    else:
        if not args.match:ap.error('--match required unless --input provided')
        import speaker_word_align as v4
        _,folder,_,_,_=v4.select(args.match)
        src=folder/'speaker-v5-preview.en.srt'
        dst=folder/'speaker-v6-preview.en.srt'
    if not src.is_file():ap.error(f'Missing input: {src}')
    if dst.exists():ap.error(f'Output exists; refusing to overwrite: {dst}')
    before=read_srt(src)
    after,joined,moved=cleanup(before)
    # Guardrail: preserve every word in original order.
    canonical=lambda cs:re.sub(r'\s+','', ''.join(c[2] for c in cs))
    if canonical(before)!=canonical(after):raise RuntimeError('Text integrity check failed; nothing written')
    for a,b,_ in after:
        if a>=b:raise RuntimeError('Invalid cue timing; nothing written')
    with dst.open('xb') as f:f.write(render(after))
    print(f'Input cues: {len(before)}; cleaned cues: {len(after)}; joined fragments: {joined}; moved trailing fragments: {moved}')
    print(f'Under 0.5s: {sum(b-a<.5 for a,b,_ in after)}; one-word cues: {sum(len(t.split())==1 for _,_,t in after)}')
    print(f'PREVIEW: {dst}')
    print('READ ONLY: No media, installed subtitles, SQLite database, original backups, or V5 preview changed.')

if __name__=='__main__':main()
