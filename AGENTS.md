# Sightbox Studio

A simple timeline video editor with one write path for a person and an agent. One track, cuts only. The cut list is a JSON file; a page in the browser is a view over it; a local Python server applies changes, serves the media and runs ffmpeg; an agent changes it the same way, through the same server, and sees the footage through ffmpeg.

## Read first

- `BRIEF.md`, the build brief, whole. It carries the decisions, the contract, the proven ffmpeg commands and the build orders.
- `README.md` for how to run it.

## Boundaries

- The cut list on disk is the truth. The page holds no state an agent cannot reach through the server.
- One write path: every change is a JSON op applied by `studio.project`. The page, the CLI, curl and MCP all go through it. Nothing else writes the project file.
- Cuts only. One track, no gaps, no overlaps. No transitions, titles, music, effects.
- Changes apply directly, attributed and journaled. No gate.
- Standard library only. No pip, no npm, no build step. One HTML file.
- ffmpeg and ffprobe run as argument lists, never as shell strings.
- The server binds 127.0.0.1 and serves media only by source id.
- Media is never copied or moved.
- Model names stay out of the repository.

## Run

```bash
python3 -m studio serve PROJECT.json          # http://127.0.0.1:3200/studio.html
python3 -m unittest discover -s tests -v
python3 tools/ffmpeg_proofs.py                # the proofs behind the brief; needs ffmpeg
```

## Layout

- `studio/` — `project` (the cut list and the write path), `media` (ffprobe and ffmpeg), `server`, the CLI in `__main__`, `mcp`
- `app/studio.html` — the page
- `tests/` — unittest; media tests skip without ffmpeg
- `tools/ffmpeg_proofs.py` — the proofs behind the brief, and the recipe for synthetic test clips
