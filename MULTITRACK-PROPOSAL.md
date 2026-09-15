# Sightbox Studio — Multi-Track Proposal (draft, unruled)

Written 15 September 2026, at Nathan's request, after he asked for the ability to
stack clips — multiple simultaneous video tracks (for picture-in-picture and
overlays) and audio tracks (for voiceover and music), composited together on
export, the way Premiere works.

**This is a proposal, not a decision.** `BRIEF.md` rules this out by name:

> Cuts only. One track. No gaps, no overlaps. No transitions, titles, music
> track, effects, speed, colour. **Those are a different product.**

That line was deliberate — it's what let Studio ship in one build order, six
proven ffmpeg commands, and one HTML file. Reopening it is a real decision,
not a build-order task, so nothing here is implemented. It needs a ruling —
the same kind of SightOps session that produced `BRIEF.md` — before any code
changes. This document exists to make that ruling easier: what would have to
change, what's cheap, what's expensive, and the specific questions only
Nathan (or whoever runs that session) can answer.

## The shape of the problem

Today's model is one list, in play order, no gaps:

```json
"cuts": [{"id": "c1", "source": "s1", "in": 1.5, "out": 4.0}]
```

`start`/`end` are *derived* — each cut's position is just the sum of every
prior cut's duration. That's what makes the model simple: there is no such
thing as "where" a cut is, only "which cut is next."

Stacking breaks that assumption at the root. A voiceover clip needs to sit at
an arbitrary point against the picture, possibly with silence before and
after it. A picture-in-picture overlay needs to start 3 seconds into the
program track and run for 5, not "the next thing in a list." Position has to
become **authored**, not derived, and clips on different tracks have to be
allowed to overlap in time — that's the entire point.

This is the same shift every timeline editor makes going from "sequence" to
"tracks," and it touches every layer: the schema, the ops, the ffmpeg graph,
the page, and the MCP tools. None of it is a small patch.

## A narrower option worth ruling on first

Before a full Premiere-style N-track model, there's a materially cheaper
**hybrid**: keep today's single video track exactly as it is now — gapless,
ordered, cuts-only, unchanged code, unchanged tests, unchanged page — and add
the ability to layer independent **overlay tracks** (video, for PiP) and
**audio tracks** (voiceover, music) on top of it, each with freely placed
clips. The program track stays the spine and sets the export duration;
everything else floats above it.

This covers what people usually mean by "stack clips" (voiceover over B-roll,
a logo or PiP webcam in the corner, a music bed) without touching the part of
Studio that already works, and it's a much smaller ffmpeg problem: overlay
and mix on top of the existing concat, not a general compositor. I'd
recommend ruling on this version specifically, not the unbounded one, unless
there's a concrete need for more.

## What would have to change, either way

**Schema.** Cuts become clips with an authored `start`, grouped into tracks:

```json
"tracks": [
  {"id": "t1", "kind": "video", "name": "Program", "clips": [
    {"id": "c1", "source": "s1", "in": 1.5, "out": 4.0, "start": 0.0}
  ]},
  {"id": "t2", "kind": "video", "name": "Overlay", "clips": [
    {"id": "c4", "source": "s3", "in": 0.0, "out": 5.0, "start": 3.0,
     "x": "iw-ow-40", "y": 40, "scale": 0.25}
  ]},
  {"id": "t3", "kind": "audio", "name": "Voiceover", "clips": [
    {"id": "c5", "source": "s4", "in": 0.0, "out": 8.0, "start": 1.0, "gain_db": 0}
  ]}
]
```

The program track keeps today's rules (gapless, list order). Every other
track allows gaps and overlapping-in-time clips against other tracks (never
against itself — two clips can't occupy the same span on one track).

**New ops**, alongside the existing nine: `add_track`, `remove_track`, and a
`place`-style op for putting a clip at an arbitrary `start` (today's `add_cut`
always appends; today's `move` reorders by index, which stops meaning
anything once position is a timestamp, not a list slot). `trim` and `split`
still make sense per-clip; `move` gets replaced or heavily changed.

**ffmpeg.** The proven export graph is `concat` after per-cut `scale/pad`.
Compositing needs `overlay=x:y:enable='between(t,start,end)'` for each PiP
layer atop the program track's output, and `amix=inputs=N` (with per-input
`volume=` for gain) for audio, replacing straight concatenation of audio
streams. This needs its own proof pass, the same way `tools/ffmpeg_proofs.py`
proved the current graph — overlay timing accuracy and audio sync are exactly
the kind of thing that looks right in a filtergraph and drifts by a frame in
practice.

**The page.** The timeline needs one row per track instead of one row, plus
add/remove-track controls and (for audio) maybe a waveform or at least a
distinct visual so it doesn't read as a video block. The bigger cost is the
**preview**: today's preview plays one `<video>` at a time, swapped at cut
boundaries — simple because only one thing is ever on screen. Real compositing
preview (seeing the PiP corner live, hearing the voiceover mixed in) needs
either several absolutely-positioned `<video>` elements layered with CSS
(cheap, works for PiP, doesn't help audio mixing) or a `<canvas>` drawing
multiple video sources per frame (more capable, more code, still no live
audio mix without the Web Audio API). I'd start with layered `<video>`
elements for picture preview and accept that audio balance is only truly
verified by scrubbing the export, not the live preview — a real, disclosed
limitation, not silently ignored.

**MCP / the agent's eyes.** `project_get` and `changes_apply` need to carry
tracks instead of a flat cut list; `source_scenes` / `source_silences` are
unaffected since they're per-source, not per-timeline. The tool surface grows
by two or three tools (`track_add`, maybe `clip_place`).

## Questions only a ruling can answer

1. **Scope**: the hybrid above (one gapless program track + free-form overlay
   and audio tracks), or a fully general N-track model where every track is
   equal? I'd rule the former unless there's a specific need for the latter.
2. **Z-order**: fixed by track array order, or an explicit field per track?
3. **Gain/volume**: per clip, per track, or both? Needed the moment there's a
   voiceover over a music bed over sync audio.
4. **PiP framing**: free-form `x`/`y`/`scale` per clip (flexible, more UI to
   build), or a small fixed set of positions ("top-right, 25%") (much less
   UI, covers the common case)?
5. **Preview fidelity**: is layered-`<video>` PiP preview with export-only
   audio-mix verification an acceptable v1, or does live audio preview matter
   enough to justify the Web Audio API work?
6. **Naming**: is this Sightbox Studio v2 — the same repo, a breaking schema
   version bump — or, per the brief's own words ("those are a different
   product"), a new sibling project that happens to share `studio/project.py`
   as a starting point? That changes whether existing single-track projects
   need a migration path.
7. **Timeline duration**: still the program track's length, or the longest
   track's?

## If this gets ruled in

I'd propose treating it as its own brief, written and proven the way
`BRIEF.md` was — its own load-bearing ffmpeg proofs (`overlay` timing,
`amix` levels, both checked against synthetic clips before any code), its own
build orders, with the existing single-track behaviour as build order 0 (already
done) rather than something rewritten from scratch. Until that ruling
happens, Studio stays exactly as built: one track, cuts only, per `BRIEF.md`.
