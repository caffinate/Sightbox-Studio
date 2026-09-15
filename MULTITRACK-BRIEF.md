# Sightbox Studio — Multi-Track A/V Editing (v2 build brief)

Written 15 September 2026. Supersedes `MULTITRACK-PROPOSAL.md`'s open questions:
Nathan ruled the shape ("editing capabilities of Premiere... clips can be
stacked so you can create an editorial workflow, so one clip moves into the
next at a specific spot" — a sequential cutaway/insert-edit workflow, J-cuts
and L-cuts, **not** simultaneous picture-in-picture compositing) and asked for
it built. The first draft of this design was then run through an adversarial
three-lens critique (data model, ffmpeg correctness, migration/UI feasibility)
before any code was written. All three lenses came back `needs_changes` with
real, would-have-shipped-broken defects — not style notes. Every blocker and
major finding below is incorporated; this is the corrected design being
implemented.

**One decision made, not just proposed:** v1's PR is open but unmerged and has
shipped to nobody. Maintaining a compatibility shim between v1's op vocabulary
and v2's would be real, ongoing complexity in service of zero actual users.
So v2 **replaces** v1's op names (`add_cut`→`add_clip`, `move`'s index-based
`to`→a timeline `start`, etc.) rather than running both side by side. This
lands as new commits continuing the same branch/PR, not a second one — v1's
work (the cut list contract, media/server/CLI/page/MCP scaffolding, the ffmpeg
proof discipline) is the foundation v2 builds on, not a parallel track.

## What the critique found (and the fix now in this design)

**Data model**
- No `link` id counter existed → `next_ids` gets a `link` counter alongside `source`/`clip`.
- `move` to a new track silently desynced a linked pair while keeping them flagged linked → a same-kind cross-track `move` now **explicitly unlinks** and reports it (`"unlinked": true`), rather than leaving a stale link.
- `add_clip`'s implicit "first track with room" target was undefined with >1 track of a kind, and computing each linked sibling's default `start` independently from its own track could desync a pair that's supposed to start together → replaced with explicit `video_track`/`audio_track` parameters (default `"v1"`/`"a1"`), and a single shared `start` computed once and applied to both.
- `split` never said which id/link survives, or the new half's `start` formula → pinned down below.
- Cascading edits (trim/move/split on a linked pair) weren't specified as atomic → **every op with a cascade validates both sides before applying either; a bad cascade refuses the whole op.**
- `remove_track` could drop the last track of a kind, leaving `add_clip`'s default targets undefined → refused.
- No way to reprioritize a video track without delete-and-rebuild → added `reorder_track`.

**ffmpeg / export** (the sharpest findings)
- Video (frame-quantized through `concat`) and audio (millisecond-exact through `adelay`) ran on two independent clocks with nothing tying them together, so sync error at cut points would grow with edit length → **both pipelines now derive their timing from the same frame-snapped breakpoint grid**, computed once, before either graph is built.
- `amix`'s default `normalize=1` divides by *total input count*, not real simultaneity — an ordinary sequential (non-overlapping) edit would get quieter as clips are added, for no acoustic reason → `normalize=0`, with a limiter after the mix to catch genuine overlap clipping.
- Nothing pinned the mixed audio bed's length to the video's length → the whole export gets an explicit `-t {duration}`, plus a silence-padding input into the mix so `amix`'s own `duration=longest` has something to be long against.
- "Reuse v1's per-segment graph unchanged" was ambiguous and wrong: the flattened video concat must be **video-only** (`concat=n=N:v=1:a=0`) with no per-segment audio branch at all, since audio now comes entirely from the separate mix.
- Degenerate sub-frame segments from the breakpoint walk need to be merged into a neighbor before reaching `concat`, or a 0-frame branch can break the filter graph.
- The black-filler input for an uncovered video interval needs the *same* filter chain (fps/pad/setsar/format) as a real segment, not a bare `color=` source, to concat cleanly.
- New proof cases are needed in `tools/ffmpeg_proofs.py` **before `media.py` is touched**, matching this repo's existing discipline: non-overlapping-audio loudness, a genuine J-cut sync check, and a black-video-gap-with-audio-still-playing case (the decoupling is intentional — say so and prove it).

**Migration / UI**
- Track ids in the schema example didn't match the id-generation scheme → **fixed**: `v1`/`a1` are hardcoded, never counter-derived; further tracks get their own per-kind counters (`v2`, `v3`... / `a2`, `a3`...).
- Migration needed to be a *pure* function of the old cut list (not stateful), so two independent readers (the page's poll, an MCP agent) loading the same not-yet-saved v1 file always synthesize identical ids → migration derives every id purely from cut index (below).
- The entire preview engine (`app/studio.html`) is built on "exactly one `<video>` is ever visible or audible, and it's the whole clip" — that's false the moment audio can come from a different, independently-trimmed clip on another track (the whole point of a J-cut) → **real rewrite**: one muted `<video>` for picture (driven by the server-computed segment list), a small pool of `<audio>` elements (one per audio clip currently covering the playhead), and a virtual clock (not any single element's `currentTime`) with periodic drift correction, since independent `HTMLMediaElement`s will drift.
- The old ">60 cuts" warning measured total clip count; the real constraint is *concurrent* audio elements at one instant → heuristic updated to match.

## Schema (version 2)

```json
{
  "version": 2, "revision": 9, "name": "Flux teaser",
  "output": {"width": 1280, "height": 720, "fps": 30.0},
  "sources": [ ...unchanged from v1... ],
  "tracks": [
    {"id": "v1", "kind": "video", "name": "V1"},
    {"id": "v2", "kind": "video", "name": "V2"},
    {"id": "a1", "kind": "audio", "name": "A1"}
  ],
  "clips": [
    {"id": "c1", "track": "v1", "source": "s1", "in": 0.0, "out": 3.0, "start": 0.0, "link": "L1"},
    {"id": "c2", "track": "a1", "source": "s1", "in": 0.0, "out": 3.0, "start": 0.0, "link": "L1"}
  ],
  "next": {"source": 2, "track_video": 2, "track_audio": 1, "clip": 3, "link": 2}
}
```

Every project always has exactly the default `v1` (video) and `a1` (audio)
track at minimum — `remove_track` refuses to drop the last of a kind. Video
track *priority* is array position: later in `tracks` = higher priority
(topmost/wins where tracks overlap in time). New video tracks are appended,
so a freshly added track is topmost by construction; `reorder_track` changes
this later without deleting anything. `start` is **authored** (an explicit
timeline second), not derived. Two clips on the *same* track must not overlap
in time; clips on *different* tracks are expected to and often will — that's
the feature. Gaps on a single track are allowed (v1 disallowed them).

`link`: shared between a video clip and an audio clip created together by one
`add_clip` call, so the UI can show/treat them as one editorial unit — a
cascading trim/move affects both while linked. `unlink` (or a same-kind
cross-track `move`, which unlinks automatically) breaks the shared id so each
can move/trim independently — this is what makes an offset J/L cut of a
single original clip possible. `remove_clip` removes exactly one clip; a
linked sibling survives, now unlinked — this **is** "a clip turns into
audio-only or video-only."

## Ops (replaces v1's `add_cut`/`trim`/`split`/`move`/`remove_cut`; `add_source`, `remove_source`, `set_output`, `rename` are unchanged)

| op | fields | result |
|---|---|---|
| `add_track` | `kind`, `name`? | `{"track": id}` — video ids `v2`, `v3`...; audio ids `a2`, `a3`...; appended (new video track is topmost) |
| `remove_track` | `track` | `{"track": id}` — refused while any clip is on it, or if it's the last track of its kind |
| `reorder_track` | `track`, `to` | `{"track": id, "to": to}` — `to` is an index among same-kind tracks; changes video priority order |
| `add_clip` | `source`, `in`?, `out`?, `start`?, `video_track`? (default `"v1"`), `audio_track`? (default `"a1"`), `with_audio`? (default `true`) | `{"clip": id, "sibling": id_or_null}` — a video clip is created iff the source has video, on `video_track`; an audio clip is created iff the source has audio and `with_audio`, on `audio_track`; both share **one** computed `start` (the param if given, else the later of the two target tracks' current ends) so a linked pair always truly starts together; refused (atomically) if either placement would overlap an existing clip on its target track |
| `trim` | `clip`, `in`?, `out`? | `{"clip": id, "sibling": id_or_null}` — same span rules as v1; if linked, the sibling's `in`/`out` are set to the *same* values (siblings always share one source and started identical, so they stay identical while linked — no delta drift) |
| `move` | `clip`, `start`, `track`? | `{"clip": id, "sibling": id_or_null, "unlinked": bool}` — with no `track`, moves this clip to `start` on its current track, and if linked, shifts the sibling by the same timeline delta on *its* track; with `track` (must be the same kind as the clip), moves just this clip and **automatically unlinks** the pair, reporting it; refused (atomically, including the cascade) on any resulting overlap |
| `unlink` | `clip` | `{"clip": id, "sibling": id_or_null}` — drops the shared link; no-op if there is no sibling |
| `split` | `clip`, `at` | `{"clip": id_of_second_half, "sibling": id_or_null}` — `at` is a source time strictly inside the clip; the first half keeps its id and link, `out` becomes `at`; the second half gets a new id, `in=at`, `out=`original out, `start = first_half.start + (at - first_half.in)` (immediately contiguous on the same track); if linked, the sibling splits at the same source time the same way, and the **two second-halves** get a fresh shared link (the two first-halves keep the original link) — pinned down exactly, not left ambiguous; refused (atomically) if a resulting placement would collide with an existing clip |
| `remove_clip` | `clip` | `{"clip": id}` — a linked sibling survives, unlinked |

`video_segments()` (new, alongside `timeline()`/`duration()` in `project.py`,
still pure — no ffmpeg): breakpoints are every video clip's `start`/`end`
across every video track; for each resulting interval, the covering clip on
the *highest-priority* video track wins; an interval no video track covers is
a filler marker. This is computed **once, server-side**, and is the single
source of truth `media.py`'s exporter, the page's preview, and an MCP agent
all read — not reimplemented three times (that was itself a major finding:
divergent reimplementations are how "the preview looks right but the export
doesn't" bugs happen). `duration()` becomes `max(end)` across all clips.

## Export (`studio/media.py`)

1. Take `video_segments()` from `project.py`. In `media.py` (which owns
   ffmpeg-specific concerns; `project.py` stays ffmpeg-agnostic per the
   original brief's boundary), snap every breakpoint to the output frame grid
   — `snapped_frame(t) = round(t * fps)` — and derive each segment's actual
   duration as a *cumulative* frame-boundary difference
   (`snapped_frame(end) - snapped_frame(start)`), not an independently
   rounded per-segment duration; this bounds total drift to ±0.5 frame across
   the whole export instead of letting it grow with segment count. Merge any
   resulting 0-frame interval into a neighbor.
2. Build the flattened video as a **video-only** concat
   (`concat=n=N:v=1:a=0[v]`) of per-segment `scale/pad/setsar/format`
   chains, exactly like v1's picture path, plus a `color=c=black:r=<fps>`
   filler (through the *same* chain) for any uncovered interval.
3. Every audio clip, on every audio track, becomes its own input: trimmed to
   its `in`/`out`, `aformat`-normalized to stereo/48k, then `adelay`'d to its
   *frame-snapped* `start` (in ms) — the same grid the video side used. All
   of these plus one silence-padding input (sized to `duration()`) feed
   `amix=inputs=N+1:duration=longest:normalize=0`, then a limiter.
4. The whole `ffmpeg` invocation gets an explicit `-t {duration():.3f}` on
   the output, pinning both mapped streams to the same authoritative length
   regardless of where any individual segment or clip happened to end.

New `tools/ffmpeg_proofs.py` cases, run and passing **before** `media.py` is
touched: three non-overlapping audio clips stay full volume (the
`normalize=1` regression this design avoids); two clips with an authored
offset produce audio landing within one video frame of where the picture
cuts (the sync check); a video gap with a still-playing audio clip underneath
produces real audio, not silence, over the black filler (the decoupling is
intentional — prove it, don't just assert it).

## Migration

A v1 project file (flat `"cuts"`, no `"tracks"`/`"clips"` key) is upgraded to
v2 **inside `Project.from_dict`**, on every load — a pure function of the old
cut list alone, so two independent readers always agree: synthesize the
default `v1`/`a1` tracks; walk the old cuts in list order, accumulating
`start` exactly as v1's `timeline()` used to derive it; for cut index `i`
(0-based), mint clip id `c{2i+1}` (video, if the source has video) and
`c{2i+2}` (audio, if the source has audio), sharing link id `L{i+1}`; set
`next_ids` (`clip`, `link`) strictly above everything just minted. `version`
becomes 2 in memory immediately; on disk it lags until the next `Store.save()`
writes it. A synthetic journal entry marks the moment of that first v2 save,
so the journal's mixed vocabulary (pre-migration v1 ops, post-migration v2
ops) has a documented boundary rather than silently changing shape.

## The page (`app/studio.html`)

One row per track (video rows above audio rows, in **reverse** priority order
— the topmost-priority video track renders as the topmost row, matching what
an editor expects to see "on top"), clips positioned by `start × zoom`
(authored, not auto-packed), a linked pair visually bracketed/same-colored
until unlinked. New controls: Add Video Track, Add Audio Track, Unlink (on a
selected clip); Delete now removes just the one clip under selection, per
`remove_clip`'s semantics. The preview becomes: one muted `<video>` for
picture, driven by the server's `video_segments()`, swapped at segment
boundaries; a small pool of `<audio>` elements, one per audio clip currently
covering the playhead, created/destroyed as clips enter/leave scope, each
seeked to `clip.in + (t − clip.start)`. Playback uses a virtual clock (not
any one element's `currentTime`) and re-seeks any element that's drifted more
than ~80ms from it — documented as a best-effort live approximation; the
ffmpeg export is the only frame/sample-accurate result. The "many cuts" warning
is now based on the maximum number of audio clips simultaneously covering any
one instant, not total clip count.

## MCP (`studio/mcp.py`)

`project_get`/`changes_apply` carry the new `tracks`/`clips`/`video_segments`
shape. Tool inputs for `changes_apply` accept the new ops above; no new tools
are strictly required (`add_track`/`reorder_track`/etc. are ops within
`changes_apply`, same as `add_source` always was), keeping the tool surface
the same size.

## Build order (continuing this branch/PR)

Same discipline as v1: proofs before `media.py`, tests green before each
commit, one thing at a time.

1. **New ffmpeg proofs** — the three cases above, added to `tools/ffmpeg_proofs.py`, run and passing.
2. **`studio/project.py` v2** — schema, the revised ops table, `video_segments()`, migration, with `tests/test_project.py` rewritten to the new op vocabulary and covering every cascade/atomicity rule above.
3. **`studio/media.py` v2 export** — the frame-snapped, video-only-concat + separate-amix graph, tested against the same synthetic clips as the new proofs.
4. **`studio/server.py`** — `describe()` carries tracks/clips/segments; `changes_apply` and export endpoints otherwise unchanged in shape.
5. **`app/studio.html`** — the multi-row timeline and the picture-video-plus-audio-pool preview rewrite.
6. **`studio/mcp.py`** — payload shape update; `tests/test_mcp.py` updated to the new ops.

Each stage gets its own commit, tests green, on `claude/tender-euler-i73nhw`.
