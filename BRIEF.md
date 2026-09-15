# Sightbox Studio — Build Brief

Written 15 September 2026, in the SightOps session that ruled the editor a separate project. Canonical copy: this file. If a copy is filed in Notion later, this file becomes the pointer, per the canonical-copies rule.

**Superseded in part.** This brief's "cuts only, one track" decision (below, under *Decisions already made*) held through the single-track build. Nathan then asked for multi-track editing; `MULTITRACK-BRIEF.md` is that brief, written and critiqued the same way this one was, and it overrides "cuts only, one track" specifically. Everything else here — the boundaries, the proven ffmpeg techniques this file's export graph is built from, the vocabulary for a source and a batch, the server/CLI/MCP shape — still holds and is assumed by `MULTITRACK-BRIEF.md` rather than repeated in it. Read this file first, then `MULTITRACK-BRIEF.md`, for the current state of the schema and ops.

## What it is

A simple timeline video editor with one write path for a person and an agent. One track, cuts only. The cut list is a JSON file on disk and is the editor's whole state. A page in the browser is a view over it. A local Python server applies changes to it, serves the media, and runs ffmpeg. An agent changes it the same way a person does, through the same server, and gets eyes on the footage through ffmpeg: single frames, contact sheets, scene changes, silences. Export is a real H.264 MP4 from ffmpeg.

It runs on a Mac, locally, with nothing installed beyond Python 3.9 or newer and ffmpeg.

## The first use

The Flux Films room carries "Build 30-second teaser and storyboard" and "Post quick test clips for Ray to view". The first job is: a person and an agent rough-cut a teaser from a folder of clips and export it. If that never happens, this was not worth building. Nothing in this brief is for a hypothetical second user.

## Decisions already made

Do not reopen these.

- **A separate project.** Not inside SightOps. The same shape as the desk: one page, one standard-library server, one write path, attributed changes. The same look, so the two tools read as siblings.
- **A web page plus local ffmpeg.** Not native Swift, not browser-only WebCodecs. It can be built and tested end to end in a cloud session; the JSON cut list is a clean agent interface; a Swift app can only be run on the Mac.
- **Cuts only.** One track. No gaps, no overlaps. No transitions, titles, music track, effects, speed, colour. Those are a different product.
- **Changes apply directly,** attributed and journaled. No gate yet. The journal is the seed for a gate later; do not build the gate.
- **Build order:** the cut list and the write path, then export and the server, then the page, then the agent eyes, then MCP. Export second, not last.
- **The building session is not the designing session.** Where this brief decides something, build it as decided. Where it is silent, pick the simplest thing that keeps the boundaries and say so in the commit message.

## Load-bearing facts, PROVEN 15 September 2026

Every ffmpeg command in this brief was run and checked on ffmpeg 6.1.1. `tools/ffmpeg_proofs.py` reproduces every proof on synthetic clips; run it before touching `studio/media.py`. Do not re-derive these as though they were unknown.

- ffmpeg 6.1 carries every filter and encoder needed: `trim`, `atrim`, `setpts`, `asetpts`, `fps`, `scale`, `pad`, `setsar`, `format`, `aformat`, `anullsrc`, `concat`, `select`, `metadata`, `showinfo`, `silencedetect`, `drawtext` (needs libfreetype; Homebrew's ffmpeg has it), `tile`; encoders `libx264`, `aac`, `mjpeg`.
- **The export graph.** Three cuts from two sources, one source silent, sizes 1280×720 at 30 fps and 640×360 at 25 fps, a 4.5 s cut list, gave an MP4 probed at 4.533 s, 1280×720, 30 fps, with audio, in 1.6 s. The 33 ms is AAC priming and frame rounding. Acceptance is a duration within 0.1 s of the cut list.
- **A single frame.** A JPEG from any source time in 0.14 s.
- **A contact sheet.** A 4×2 grid with timestamps burnt in at 0.000, 0.500 … 3.500 s, inspected by eye.
- **Scene changes.** `select='gt(scene,T)'` followed by `metadata=print` gives each selected frame's `pts_time` and `lavfi.scene_score`. Solid black to white scores 1.0; solid red to blue scores 0.40 (the score is luma only); a clip with no cut returns nothing. Default threshold 0.3.
- **Silences.** `silencedetect=noise=-30dB:d=0.5` on a clip with a one-second gap reports `silence_start: 1.49991` and `silence_end: 2.50009`.
- **Python's `SimpleHTTPRequestHandler` ignores `Range` headers.** The `<video>` element needs them to seek, and Safari refuses to play at all without them. The server implements Range itself; there is no library to lean on.
- **The cloud build environment** has no ffmpeg preinstalled; `apt-get install -y ffmpeg` works there (proven, 6.1.1). Its Playwright Chromium may lack H.264 decoding, so a page test that needs playback must use VP9/WebM synthetic clips (`libvpx-vp9` is present) or test the DOM only. On the Mac, `brew install ffmpeg`.
- **The GitHub App used by cloud sessions cannot create repositories.** This repository was created by hand on 15 September 2026.

## Boundaries

- The cut list on disk is the truth. The page holds no state an agent cannot reach by reading that file through the server.
- One write path. Every change is a JSON op applied by `studio.project`. The page, the CLI, curl and MCP all go through it. There is no second path, and nothing writes the project file except `studio.project.Store`.
- Cuts only, one track, in list order, no gaps, no overlaps.
- Standard library only. No pip, no npm, no build step. One HTML file with vanilla JavaScript.
- ffmpeg and ffprobe run as argument lists through `subprocess`, never as shell strings. Their stderr tail goes into the error message on failure.
- The server binds `127.0.0.1` only. It serves media only for registered sources, by source id, never by path.
- Media is never copied or moved. A source is a reference to a file where it already is.
- Every batch is attributed (`by`) and journaled. Nothing is applied silently.
- Model names do not go into the repository.

## Vocabulary

| Word | Meaning |
|---|---|
| source | A media file, registered by id (`s1`, `s2` …). Never modified. |
| cut | A span of one source, `in` to `out` in source seconds, placed on the track. Ids `c1`, `c2` … |
| cut list, project | The JSON file: name, output, sources, cuts. |
| track, timeline | The cuts in list order. `start` and `end` in timeline seconds are derived, never stored. |
| output | Width, height and fps of the export. Defaults to the first source added. |
| journal | `<project stem>.journal.jsonl` beside the project file. One line per batch. |
| by | Who sent a batch: `person` from the page, `cli` from the command line, `agent` from MCP, whatever a curl caller says (default `agent`). |
| eyes | What an agent gets from ffmpeg: frame, sheet, scenes, silences. |

## Layout

```
studio/
  __init__.py
  __main__.py     the CLI
  project.py      the cut list and the write path; no ffmpeg here       (in the repo, tested)
  media.py        ffprobe and ffmpeg: probe, export, frame, sheet, scenes, silences
  server.py       the local server: page, Range media, JSON API
  mcp.py          the MCP stdio server over the same operations
app/studio.html   the timeline page, one file, vanilla JavaScript
tests/            unittest; media tests skip when ffmpeg is absent
tools/ffmpeg_proofs.py   the proofs behind this brief; also the recipe for synthetic clips
BRIEF.md  AGENTS.md  README.md  .gitignore
```

## The cut list

`studio/project.py` is in the repository with `tests/test_project.py`. It is the contract in executable form. Read it before anything else. The tables below describe it; the code wins on any detail the tables leave out.

```json
{
  "version": 1,
  "revision": 14,
  "name": "Flux teaser",
  "output": {"width": 1920, "height": 1080, "fps": 29.97},
  "sources": [
    {"id": "s1", "path": "wide.mov", "name": "wide.mov", "duration": 12.417,
     "width": 1920, "height": 1080, "fps": 29.97, "has_audio": true},
    {"id": "s2", "path": "/Volumes/Footage/ray-01.mp4", "name": "ray-01.mp4", "duration": 41.2,
     "width": 1080, "height": 1920, "fps": 30.0, "has_audio": true}
  ],
  "cuts": [
    {"id": "c1", "source": "s1", "in": 1.5, "out": 4.0},
    {"id": "c3", "source": "s2", "in": 10.0, "out": 12.25},
    {"id": "c2", "source": "s1", "in": 8.0, "out": 12.417}
  ],
  "next": {"source": 3, "cut": 4}
}
```

Rules:
- `revision` goes up by one on every save. It is the version the page polls; a change from anywhere shows on the page within the poll interval.
- Ids come from `next` and are never reused.
- Times are seconds, rounded to milliseconds. A cut is at least 0.02 s long. `out` may run past a source's probed duration by at most 0.05 s.
- A source path inside the project's folder is stored relative to it, so the folder can move. Any other path is stored absolute. `~` is expanded.
- Adding the same file twice returns the existing id; it does not add a second source.
- `output` is set from the first source added, with rotation applied (a portrait phone clip gives 1080×1920), and can be changed with `set_output`.
- A source cannot be removed while a cut uses it.

The journal, one line per batch:

```json
{"at": "2026-09-15T10:12:03-0700", "by": "person", "changes": [{"op": "trim", "cut": "c1", "out": 3.8}], "results": [{"cut": "c1"}]}
```

## The ops

A batch is `{"by": "…", "changes": [ … ]}`. It applies whole or not at all. Results come back one per change, in order. An error names the index and the op: `change 1 (trim): out 9.3 is past the end of wide.mov (8.2 s)`.

| op | fields | rules | result |
|---|---|---|---|
| `add_source` | `path` | the file must exist and have a video stream (ffprobe decides); idempotent per file; sets `output` if unset | `{"source": "s3"}`, or `{"source": "s1", "existing": true}` |
| `remove_source` | `source` | refused while any cut uses it; the message names the cuts | `{"source": "s3"}` |
| `add_cut` | `source`, `in`?, `out`?, `at`? | defaults: the whole source, at the end; `at` is 0 … number of cuts | `{"cut": "c4"}` |
| `trim` | `cut`, `in`?, `out`? | the span rules above | `{"cut": "c4"}` |
| `split` | `cut`, `at` | `at` is a source time at least 0.02 s inside both ends; the first half keeps the id, the second half is a new cut inserted right after | `{"cut": "c5"}`, the second half |
| `move` | `cut`, `to` | `to` is 0 … number of cuts − 1 | `{"cut": "c4", "to": 0}` |
| `remove_cut` | `cut` | | `{"cut": "c4"}` |
| `set_output` | `width`?, `height`?, `fps`? | even integers of at least 16; fps at least 1 | `{"output": {…}}` |
| `rename` | `name` | non-empty | `{"name": "…"}` |

The page thinks in timeline time and converts before it sends: a split at the playhead sends `at = cut.in + (T − cut.start)`. An agent thinks in source time, which is what scenes and silences return, so it sends those numbers straight through.

## The local API

`python3 -m studio serve project.json [--port 3200] [--media DIR]`. Binds `127.0.0.1`. The project file is reloaded from disk on every request; the file is the truth. `Store` holds a `threading.Lock` across load, apply, save and journal for a POST.

| Method and path | Request | Response |
|---|---|---|
| `GET /`, `GET /studio.html` | | the page |
| `GET /api/project` | | the describe payload below |
| `POST /api/changes` | `{"by": "…", "changes": […]}` | `{"results": […], …describe}`; on any failure 400 `{"error": "…"}` and nothing saved |
| `POST /api/export` | `{"path"?: "…", "preset"?: "medium"}` | `{"path", "bytes", "seconds", "duration"}`; default path `<dir>/<slug of name>.mp4`; presets are libx264's; 500 with ffmpeg's stderr tail on failure; synchronous |
| `GET /api/media` | | `{"dir", "files": [{"name", "path", "bytes", "source"}]}`: the folder's video files, not recursive, hidden files skipped, `source` set when already added |
| `GET /media/<source id>`, and `HEAD` | `Range` honoured | the file; 206 with `Content-Range: bytes S-E/size` on a range, 200 whole otherwise; `Accept-Ranges: bytes`; `Content-Type` by extension (mp4 `video/mp4`, mov `video/quicktime`, m4v `video/x-m4v`, webm `video/webm`, mkv `video/x-matroska`, else `application/octet-stream`); 1 MiB chunks; 404 for an unknown id or a missing file; 416 for a range that starts past the end; a client that disconnects mid-stream is swallowed, not logged as an error |
| `GET /api/frame?source=&t=&width=640` | | `image/jpeg`; cached in memory by (source, t to 0.01 s, width), at most 200 entries |
| `GET /api/sheet?source=&cols=6&rows=5&width=1920` | | `image/jpeg`; the tile arithmetic in an `X-Sheet` header: `tiles=30 cols=6 interval=1.234` |
| `GET /api/scenes?source=&threshold=0.3` | | `{"source", "threshold", "scenes": [{"t", "score"}]}` |
| `GET /api/silences?source=&noise=-30&min=0.5` | | `{"source", "silences": [{"start", "end"}]}`; a source without audio gives an empty list |

Range forms to handle: `bytes=S-E`, `bytes=S-`, `bytes=-N`. Only the first range of a multi-range header; ignore the rest.

The describe payload:

```json
{
  "version": "14",
  "file": "/Users/nathan/Movies/flux/project.json",
  "dir": "/Users/nathan/Movies/flux",
  "project": { "…": "the cut list" },
  "timeline": [
    {"cut": "c1", "source": "s1", "in": 1.5, "out": 4.0, "start": 0.0, "end": 2.5, "duration": 2.5},
    {"cut": "c3", "source": "s2", "in": 10.0, "out": 12.25, "start": 2.5, "end": 4.75, "duration": 2.25}
  ],
  "duration": 4.75
}
```

Driving it with curl from Claude Code on the Mac, no MCP needed:

```bash
curl -s localhost:3200/api/project | python3 -m json.tool
curl -s -X POST localhost:3200/api/changes -H 'Content-Type: application/json' \
  -d '{"by":"agent","changes":[{"op":"add_cut","source":"s1","in":1.5,"out":4.0}]}'
curl -s -X POST localhost:3200/api/export -H 'Content-Type: application/json' -d '{}'
```

## The CLI

```
python3 -m studio new PROJECT.json [--name NAME]
python3 -m studio add PROJECT.json FILE...          add_source for each; --cut also appends a whole cut of each
python3 -m studio apply PROJECT.json [JSON | -]     a batch from the argument or stdin; by "cli"
python3 -m studio show PROJECT.json                 the timeline as a table: cut, source, in, out, start, end
python3 -m studio export PROJECT.json [OUT.mp4] [--preset medium]
python3 -m studio serve PROJECT.json [--port 3200] [--media DIR]
python3 -m studio probe FILE                        what ffprobe says, as the source fields
python3 -m studio frame PROJECT.json SOURCE T [--width 640] [-o out.jpg]
python3 -m studio sheet PROJECT.json SOURCE [--cols 6] [--rows 5] [-o out.jpg]
python3 -m studio scenes PROJECT.json SOURCE [--threshold 0.3]
python3 -m studio silences PROJECT.json SOURCE [--noise -30] [--min 0.5]
python3 -m studio mcp PROJECT.json                  the MCP server on stdio
```

The CLI and the server both go through `Store.apply`. A CLI write while the server is running is fine: the server reloads on every request.

## ffmpeg, the proven commands

All calls are argument lists. `FFMPEG` and `FFPROBE` come from the environment variables `STUDIO_FFMPEG` and `STUDIO_FFPROBE` when set, else `ffmpeg` and `ffprobe` on PATH. `media.available()` says whether both are present; tests skip when they are not.

**Probe.** `ffprobe -v error -print_format json -show_format -show_streams PATH`. The video stream is the first `codec_type == "video"` whose `disposition.attached_pic` is 0; none means "no video stream", a ChangeError. `fps` is `avg_frame_rate` as a fraction (`30000/1001` gives 29.97). Rotation is `side_data_list[].rotation` or `tags.rotate`; when it is ±90 or ±270, swap width and height. `duration` is `format.duration`. `has_audio` is whether any stream is audio.

**Export.** One `-ss IN -t (OUT−IN) -i PATH` per cut (input seeking is accurate in ffmpeg 6 and avoids decoding a whole file for a cut near its end), then the graph below, then concat. The proven argument list for three cuts, verbatim:

```
ffmpeg -hide_banner -v error -y
  -ss 0.500 -t 2.000 -i a.mp4
  -ss 0.000 -t 1.500 -i b.mov
  -ss 3.000 -t 1.000 -i a.mp4
  -filter_complex "
    [0:v]setpts=PTS-STARTPTS,fps=30,scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v0];
    [0:a]asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a0];
    [1:v]setpts=PTS-STARTPTS,fps=30,scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v1];
    anullsrc=r=48000:cl=stereo:d=1.500[a1];
    [2:v]setpts=PTS-STARTPTS,fps=30,scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v2];
    [2:a]asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a2];
    [v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]"
  -map "[v]" -map "[a]"
  -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p
  -c:a aac -b:a 192k -movflags +faststart out.mp4
```

Width, height and fps come from `output`. A source without audio gets `anullsrc` for the cut's duration so concat always sees audio. The graph is one string with stages joined by `;`, and no line breaks. Default preset `medium`; the API and CLI accept any libx264 preset. Measured: 4.533 s for a 4.5 s cut list.

**Frame.** `ffmpeg -v error -ss T -i PATH -frames:v 1 -vf scale=W:-2 -f image2 -c:v mjpeg -q:v 3 pipe:1`. JPEG bytes on stdout, starting `FF D8 FF`. 0.14 s.

**Contact sheet.** `interval = duration / (cols × rows)`, then:

```
ffmpeg -v error -i PATH
  -vf "fps=<1/interval, 6 decimals>,scale=<width // cols>:-2,drawtext=text='%{pts\:hms}':x=8:y=8:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=4,tile=<cols>x<rows>:padding=2:margin=2:color=black"
  -frames:v 1 -f image2 -c:v mjpeg -q:v 4 pipe:1
```

In Python the drawtext stage is written `"drawtext=text='%{pts\\:hms}'…"` so one literal backslash reaches ffmpeg; the colon inside `%{pts:hms}` must be escaped or ffmpeg reads it as an option separator. Tile r, c (zero-based, row-major) shows source time `(r × cols + c) × interval`; return that arithmetic alongside the image. If ffmpeg fails on the drawtext stage (a build without libfreetype), run again without it; the arithmetic still tells the agent the times. Defaults: 6 × 5, width 1920.

**Scene changes.** `ffmpeg -v info -i PATH -vf "scale=320:-2,select='gt(scene,THRESHOLD)',metadata=print" -an -f null -`. Downscale first; it decodes the whole file. Parse stderr: each selected frame prints a line containing `pts_time:2` followed by a line containing `lavfi.scene_score=0.399997`. Pair them into `[{"t": 2.0, "score": 0.4}]`. Default threshold 0.3; the agent can lower it to catch soft cuts or raise it to keep only hard ones.

**Silences.** `ffmpeg -v info -i PATH -vn -af silencedetect=noise=-30dB:d=0.5 -f null -`. Parse `silence_start: X` and `silence_end: Y` from stderr into `[{"start": 1.5, "end": 2.5}]`. A start with no end closes at the source's duration. A source with no audio returns an empty list without running ffmpeg.

**Synthetic clips for tests**, made by `tools/ffmpeg_proofs.py` from `-f lavfi` sources, no fixtures checked in: `a.mp4`, 4 s, solid red then solid blue from 2.0 s, a tone with a silent second from 1.5 to 2.5; `b.mov`, 3 s, `testsrc2` 640×360 at 25 fps, no audio; `c.mp4`, 4 s, black then white from 2.0 s, for scene detection at the default threshold.

## The page

`app/studio.html`. One file. No framework. It looks like the desk: the same tokens (appendix), the same fonts, dark mode by `prefers-color-scheme`. Minimum width 1100 px; it is a desk tool, not a phone page.

Layout, left to right and top to bottom:
- **Top bar.** "Studio" and the project name; the project file path in mono; a status area showing the last message, errors in the hot colour; an Export button.
- **Bin**, a left column. Sources: one row per source with name, duration and a small remove control; clicking a row appends a whole cut of it to the track. Add a source: a path field and a button. In the folder: the files from `/api/media`; clicking one not yet added adds it; one already added shows its id.
- **Preview.** A 16:9 black box holding one `<video>` element per cut (see below); only the active one is visible. The current time is drawn over it in mono.
- **Transport.** Start, one frame back, play/pause, one frame forward, end. A time readout `m:ss.cc / m:ss.cc`. Split and Remove buttons. Zoom out, zoom in, fit.
- **Timeline.** A horizontally scrolling strip: a ruler with ticks at a sensible interval for the zoom, one track, cut blocks proportional to duration showing the source name and duration, an 8 px trim handle at each edge, a playhead line spanning ruler and track. The selected cut is outlined in the accent colour.
- **Inspector.** The selected cut: source name, `in` and `out` as editable numbers (a change sends `trim`), duration, position on the track.

Behaviour:
- Click or drag on the ruler or the empty track scrubs: the playhead follows and the preview seeks.
- Click a cut to select it. Drag its body to reorder; the drop index comes from the block's centre; release sends `move` if the index changed.
- Drag a handle to trim. The block and the preview frame follow live from local state; release sends `trim`.
- Keys, ignored while typing in a field: Space play/pause; ← → one frame (1 / output fps), with Shift one second; Home and End; S split at the playhead; Delete or Backspace remove the selected cut; − and + zoom; F fit.
- Export prompts for a path with the default filled in, posts, shows "Exporting…" then the path and size.
- Every 1.5 s the page fetches `/api/project`; if `version` differs from the one it holds and no drag is in progress, it adopts the new state and re-renders, keeping the playhead and the selection. This is how an agent's changes appear without a reload.
- A 400 from the server is shown in the status area with its message. Nothing is swallowed.

Preview mechanics:
- One `<video preload="auto" muted playsinline>` per cut, `src="/media/<source id>"`, created when the cut first appears, removed when the cut goes. Idle elements are parked at their cut's `in` (after `loadedmetadata`, and again whenever `in` changes).
- `locate(T)` finds the cut whose `[start, end)` holds the playhead; at or past the end, the last cut.
- Play: the active element is unmuted and plays. A `requestAnimationFrame` loop maps `element.currentTime − cut.in + cut.start` back to the playhead. Within 20 ms of `cut.out` it pauses the element, re-parks it at `in`, and starts the next cut's element, which is already parked at its own `in`. At the end of the last cut it stops and shows the last frame.
- Scrub: seek the active element to `cut.in + (T − cut.start)`.
- Above 60 cuts, say so in the status area; browsers cap live media elements.

The Flux test clips are likely iPhone HEVC: Safari plays them, Chrome on a Mac may. Preview only; export is unaffected because ffmpeg decodes HEVC. Say so in the README.

## The MCP server

`python3 -m studio mcp PROJECT.json`. Standard library only: a stdio JSON-RPC 2.0 server, one JSON message per line, in on stdin, out on stdout. Nothing but protocol messages is ever written to stdout; logging goes to stderr. `by` is `agent` for every batch it applies.

Methods: `initialize` (reply with `protocolVersion` echoing the client's when it is one the server knows, else `2025-06-18`; `capabilities: {"tools": {}}`; `serverInfo: {"name": "studio", "version": …}`); `notifications/initialized` (no reply); `ping` (empty result); `tools/list`; `tools/call`. An unknown method gets JSON-RPC error −32601. A tool failure (a ChangeError, an ffmpeg error) is a normal result with `isError: true` and the message as text, never a JSON-RPC error.

| Tool | Input | Returns |
|---|---|---|
| `project_get` | | text: the describe payload as JSON |
| `sources_list` | | text: id, name, duration, size, audio, per source |
| `media_list` | | text: the folder's files and which are added |
| `source_add` | `path` | text: the result |
| `source_probe` | `path` | text: the probe, without adding |
| `source_frame` | `source`, `t`, `width`? (640) | an image (JPEG, base64) plus text "s1 at 2.500 s" |
| `source_sheet` | `source`, `cols`? (6), `rows`? (5), `width`? (1920) | an image plus text with the tile arithmetic |
| `source_scenes` | `source`, `threshold`? (0.3) | text: a JSON list of `{t, score}` |
| `source_silences` | `source`, `noise_db`? (−30), `min_duration`? (0.5) | text: a JSON list of `{start, end}` |
| `changes_apply` | `changes` | text: the results and the new timeline |
| `export` | `path`?, `preset`? | text: path, bytes, seconds, duration |

Every tool has a JSON Schema `inputSchema` with `required` set. Image content is `{"type": "image", "data": "<base64>", "mimeType": "image/jpeg"}`.

Registering it in Claude Code on the Mac, from the repo folder: `claude mcp add studio -- python3 -m studio mcp /Users/nathan/Movies/flux/project.json`. For Claude Desktop, the same command and args in `claude_desktop_config.json`.

An agent with these tools and a folder of clips can: list the media, add the sources, look at each sheet, read the scenes and silences, apply a batch of `add_cut` and `trim` ops that assembles a 30-second rough cut, export, and tell the person where the file is. That is the acceptance test for build order 5.

## Build orders

One order at a time. Tests green before the next. At least one commit per order. Each order names its acceptance; do not move on without it.

**0. Scaffold.** README (what it is, how to run, ffmpeg install, the HEVC note), `.gitignore`, the `tests` folder, and `python3 -m unittest discover -s tests -v` running. Most of this is in the repo already; check it and finish it.

**1. The cut list and the write path.** `studio/project.py` and `tests/test_project.py` are in the repo. Read both against this brief. Run the tests. Fix anything that disagrees with the brief, in favour of the brief. Acceptance: tests green covering every op, every validation error, batch atomicity, relative paths, the journal, and `revision`.

**2. Media, server, CLI.** `studio/media.py` (`available`, `probe`, `export`), `studio/server.py` (page, describe, changes, export, media list, Range), `studio/__main__.py` (`new`, `add`, `apply`, `show`, `export`, `serve`, `probe`). Tests: `test_media.py` makes the synthetic clips, checks the probe fields and an export duration within 0.1 s, and skips without ffmpeg; `test_server.py` runs the server on an ephemeral port and checks describe, a batch, a failing batch (400 and the file unchanged), and Range (`bytes=10-19` gives 206 with those ten bytes and the right `Content-Range`; `bytes=-5`; `bytes=990-`; a start past the end gives 416; HEAD; an unknown id gives 404). Acceptance: tests green, and from a folder with two clips `new`, `add --cut` and `export` produce an MP4 that plays.

**3. The page.** `app/studio.html` to the section above. Acceptance: with three clips in a folder, a person can add, place, trim, split, reorder, scrub, play and export from the page, and a batch sent with curl appears on the page within 2 s. A Playwright smoke test in `tests/`, skipped when Playwright is absent, checks that the page loads with no console errors, a source click adds a cut, S splits it, Delete removes it. Report to the person after this order, then continue.

**4. Agent eyes.** `frame`, `sheet`, `scenes`, `silences` in `media.py`; the four endpoints; the four CLI commands. Tests on the synthetic clips: frame magic bytes; sheet magic bytes and the arithmetic; scenes on `c.mp4` finds 2.0 at 0.3 and nothing on a still clip; silences on `a.mp4` finds one span from about 1.5 to about 2.5. Acceptance: tests green, and one sheet looked at.

**5. MCP.** `studio/mcp.py` and `tests/test_mcp.py` driving the handler in-process: initialize, tools/list, `project_get`, `changes_apply`, `source_frame` returning an image item when ffmpeg is present, a ChangeError coming back as `isError`. Acceptance: tests green, then the walk-through at the end of the MCP section, done from Claude Code on the Mac.

## Out of scope

The gate (accept, edit, respond, ignore); undo; a second track; transitions; titles; a music track; effects; speed; colour; proxies; uploads; anything that copies media; anything that needs pip or npm. Thumbnails on the blocks are allowed in order 4 only as a background image from `/api/frame` at `in`, refreshed when a trim ends, and only if they cost under an hour.

## Known risks

- Variable frame rate phone clips: the `fps` filter normalises; small drift against the preview is acceptable.
- Export is synchronous. A long export holds the request open. If that hurts, the fix is a job thread and a status poll, not a queue.
- Two writers: the lock covers threads in one process. A CLI write while the server is mid-batch can lose one of the two; the atomic replace means the file is never torn. Rare, and the journal shows what happened.
- The page's polling and per-cut video elements are fine to 60 cuts. Beyond that is a different design.
- Google Fonts: the page works offline on the fallback stacks.

## Starting a build session

Open a session on this repository with this prompt:

> Read AGENTS.md and BRIEF.md before anything else. Build Sightbox Studio to the brief, build order by build order, starting at 0. Tests green before each commit. The brief's proven commands are proven; run `tools/ffmpeg_proofs.py` once, then use them as written. Standard library only. Where the brief decides, build as decided; where it is silent, choose the simplest thing that keeps the boundaries and say so in the commit message. Report after build order 3, then continue through 5.

## Appendix: the desk's tokens

Copied from the desk so the two tools match. Fonts from Google Fonts: Archivo 500 to 800, IBM Plex Sans 400 to 600, IBM Plex Mono 400 to 600, with the fallbacks below.

```css
:root{
  --ground:#EFF1F2; --surface:#FFFFFF; --rail:#E7EBED; --sunk:#E3E8EA; --spine:#DDE3E6;
  --ink:#14181C; --muted:#5A646C; --faint:#87919A; --rule:#CCD4D8; --hair:#DEE4E7;
  --accent:#1D5C7A; --accent-ink:#FFFFFF; --accent-soft:#D8E6ED;
  --signal:#A2620C; --signal-soft:#F6E8D1; --signal-line:#D2A14E;
  --hot:#8F3413; --hot-soft:#F4DBD0;
  --good:#28654A; --good-soft:#D6E9DE;
  --new:#7A3E86; --new-soft:#EDDFF0;
  --dead:#8A9298;
  --display:"Archivo","Helvetica Neue",Arial,sans-serif;
  --body:"IBM Plex Sans","Segoe UI",Helvetica,Arial,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
}
@media (prefers-color-scheme:dark){ :root{
  --ground:#0D1114; --surface:#151B1F; --rail:#111618; --sunk:#1C2429; --spine:#192025;
  --ink:#E2E7E9; --muted:#939EA5; --faint:#6C777E; --rule:#283238; --hair:#1F272C;
  --accent:#74B6D4; --accent-ink:#0A1013; --accent-soft:#19323E;
  --signal:#DFAC5E; --signal-soft:#332614; --signal-line:#84632A;
  --hot:#E08A66; --hot-soft:#3A2018;
  --good:#7FC3A0; --good-soft:#172D23;
  --new:#C79BD0; --new-soft:#2E2033;
  --dead:#5C666C;
}}
```
