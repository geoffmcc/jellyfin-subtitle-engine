#!/usr/bin/env python3
"""Read-only sentence/reply-aware speaker splitting preview using existing v4 word timings.

Run in project folder alongside speaker_word_align.py and speaker_finalize.py:
    python speaker_sentence.py preview --match 'Ted 2024'

Requires completed v4 transcribe step. Does not run GPU, modify a media subtitle,
change the queue database or overwrite earlier previews. Original SRT text remains.
A non-speaker-aware sentence boundary is only a candidate, not a claim that the
speaker changed. The purpose is to avoid combining fast dialog exchanges.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
import speaker_word_align as v4

PUNCT = re.compile(r'''[.!?…]["'”’)]*$''')
# Very short utterances may legitimately take only a fraction of a second.
REPLY = re.compile(r'''(?i)^(?:no|yes|nah|yeah|yep|nope|ok|okay|what|why|who|huh|hey|wait|stop|sure|right|thanks|sorry|shoot|fuck\s+off|fuck\s+you|shut\s+up)[.!?…]*$''')


def word_boundary(left, right, start, end):
    """Return plausible cut time, speech gap, or None."""
    if left['start'] > right['start'] or left['end'] > right['end'] + .15:
        return None
    gap = right['start'] - left['end']
    if gap < -.22 or gap > 2.0:
        return None
    t = max(left['end'], min(right['start'], (left['end'] + right['start']) / 2))
    if t <= start + .14 or t >= end - .14:
        return None
    return t, gap


def classify_boundary(prev_text, next_text, left_words, right_words, gap, near_turn):
    # Never split in the middle of unpunctuated speech merely due to noisy
    # diarization; this caused "That's what" / "I'm doing" in earlier versions.
    end_sentence = bool(PUNCT.search(prev_text))
    short_reply = bool(REPLY.fullmatch(prev_text.strip()))
    next_reply = bool(re.match(r'(?i)^(?:no|yes|nah|yeah|nope|what|fuck|oh|wait|stop)\b', next_text.strip()))
    # A diarization transition aligned with adjacent word timestamps is stronger
    # than punctuation. Short replies are often unpunctuated (what/yeah/no).
    if near_turn:
        if -.18 <= gap <= 1.10 and left_words >= 1 and right_words >= 1:
            return 5
        return None
    # Without a turn transition, require a genuine pause for short acknowledgements.
    if short_reply and gap >= .08:
        return 4
    if next_reply and gap >= .12:
        return 3
    if not end_sentence:
        return None
    if gap >= .12:
        return 3
    if gap >= .04 and left_words >= 3 and right_words >= 2:
        return 2
    # Avoid chopping consecutive sentences from the same monologue on a
    # zero-gap boundary unless a separate speaker signal exists.
    return None


def split_cues(cues, words, turns):
    flat, anchors = v4.align(cues, words)
    by_cue = {}
    for i, x in enumerate(flat):
        by_cue.setdefault(x['cue'], []).append(i)
    out=[]
    candidate_total=0
    splits=0
    for cue_idx, (start, end, txt) in enumerate(cues):
        indices=by_cue.get(cue_idx, [])
        if len(indices)<3 or end-start < .60:
            out.append((start,end,txt)); continue
        tokens=[flat[i]['token'] for i in indices]
        turn_times=v4.transitions(turns,start,end)
        choices=[]
        for pos in range(1,len(indices)):
            li,ri=indices[pos-1],indices[pos]
            if li not in anchors or ri not in anchors:
                continue
            boundary=word_boundary(anchors[li],anchors[ri],start,end)
            if boundary is None:
                continue
            t,gap=boundary
            prior=tokens[pos-1].group()
            nxt=tokens[pos].group()
            near_turn=any(abs(t-x)<.50 for x in turn_times)
            score=classify_boundary(prior,nxt,pos,len(indices)-pos,gap,near_turn)
            if score is None: continue
            # Avoid leaving a single unpunctuated word like "Try" stranded.
            prefix=txt[tokens[0].start():tokens[pos-1].end()].strip()
            suffix=txt[tokens[pos].start():tokens[-1].end()].strip()
            if len(suffix.split())==1 and not REPLY.fullmatch(suffix):
                continue
            if len(prefix.split())==1 and not REPLY.fullmatch(prefix):
                continue
            choices.append((pos,t,score))
        candidate_total+=len(choices)
        # Pick promising positions in time order and enforce a readable minimum
        # duration between cuts. Keep legitimate very short complete replies.
        chosen=[]
        for pos,t,score in sorted(choices,key=lambda x:(-x[2],x[0])):
            if any(abs(t-x[1])<.28 or pos==x[0] for x in chosen):continue
            chosen.append((pos,t,score))
        chosen.sort()
        # Remove cuts that create unusably brief segments, lowest confidence first.
        while chosen:
            edges=[(0,start,0)]+chosen+[(len(indices),end,0)]
            bad=[]
            for j in range(len(edges)-1):
                a,ta,_=edges[j]; b,tb,_=edges[j+1]
                segment=txt[tokens[a].start():tokens[b-1].end()].strip() if b>a else ''
                minimum=.21 if REPLY.fullmatch(segment) else .48
                if not segment or tb-ta<minimum:
                    bad.append(j)
            if not bad:break
            j=bad[0]
            options=[]
            if j>0:options.append(j-1)
            if j<len(chosen):options.append(j)
            if not options: chosen=[];break
            discard=min(options,key=lambda i:chosen[i][2])
            chosen.pop(discard)
        if not chosen:
            out.append((start,end,txt));continue
        edges=[(0,start,0)]+chosen+[(len(indices),end,0)]
        parts=[]
        for (a,ta,_),(b,tb,_) in zip(edges,edges[1:]):
            segment=txt[tokens[a].start():tokens[b-1].end()].strip()
            parts.append((ta,tb,segment))
        # Preserve exact word sequence (ignoring whitespace changes): safety check.
        if re.sub(r'\s+','', ''.join(p[2] for p in parts)) != re.sub(r'\s+','',txt):
            out.append((start,end,txt));continue
        out.extend(parts);splits+=len(parts)-1
    return out,len(anchors),len(flat),candidate_total,splits


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=['preview'])
    ap.add_argument('--match',required=True)
    args=ap.parse_args()
    row,folder,original,speaker_file,_=v4.select(args.match)
    wfile=folder/'speaker-v4-words.json'
    if not wfile.is_file(): ap.error(f'Need word timestamps: {wfile}')
    data=json.loads(wfile.read_text(encoding='utf8'))
    if data.get('failed_ranges'):
        print(f'WARNING: {len(data["failed_ranges"])} intervals have no aligned words')
    cues=v4.base.parse_srt(original.read_bytes())
    turns=json.loads(speaker_file.read_text(encoding='utf8'))
    if isinstance(turns,dict):turns=turns['turns']
    output,matched,total,candidates,splits=split_cues(cues,data['words'],turns)
    dest=folder/'speaker-v5-preview.en.srt'
    if dest.exists():
        raise SystemExit(f'Preview already exists; no overwrite: {dest}')
    with dest.open('xb') as f:f.write(v4.base.render(output))
    print(f'Title: {row["title"]}')
    print(f'Original cues: {len(cues)}; aligned words: {matched}/{total} ({100*matched/max(total,1):.1f}%)')
    print(f'Sentence/reply candidates: {candidates}; accepted splits: {splits}; final cues: {len(output)}')
    print(f'Under 0.5s: {sum(b-a<.5 for a,b,_ in output)}; one-word cues: {sum(len(t.split())==1 for _,_,t in output)}')
    print(f'PREVIEW: {dest}')
    print('READ-ONLY PREVIEW: Jellyfin files, SQLite, saved originals, and prior previews unchanged.')

if __name__=='__main__':main()
