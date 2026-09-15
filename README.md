# Sightbox Studio

A timeline video editor with one write path for a person and an agent. Multiple video
and audio tracks, clips positioned in time, a linked video+audio pair per source that
can be independently unlinked (so a clip becomes audio-only or video-only), and
picture-priority resolution across overlapping video tracks for cutaway/insert-edit
workflows (J-cuts, L-cuts). The cut list is a JSON file; a page in the browser is a view
over it; a local Python server applies changes, serves the media and runs ffmpeg; an
agent changes it the same way, through the same server, and sees the footage through
ffmpeg: frames, contact sheets, scene changes, silences. Export is a real H.264 MP4,
picture and mixed audio both pinned to the same frame-accurate clock.

The original build brief is `BRIEF.md` (single-track); multi-track editing is specified
in `MULTITRACK-BRIEF.md`, which supersedes `BRIEF.md`'s "cuts only, one track" decision.
Read both before building. The boundaries are in `AGENTS.md`.

## Run

```bash
brew install ffmpeg                                                  # once
python3 -m studio new ~/Movies/flux/project.json --name "Flux teaser"
python3 -m studio add ~/Movies/flux/project.json ~/Movies/flux/*.mov --cut
python3 -m studio serve ~/Movies/flux/project.json                   # http://127.0.0.1:3200/studio.html
python3 -m studio export ~/Movies/flux/project.json
python3 -m unittest discover -s tests -v
```

Python 3.9 or newer and ffmpeg. Nothing else.

## State

All build orders in `BRIEF.md` are in the repository: the cut list and the write
path, media and the server and CLI, the page, the agent's eyes (frame, sheet,
scenes, silences), and MCP. `MULTITRACK-BRIEF.md`'s build order is also complete:
tracks, linked clips, `video_segments()` priority resolution, the multi-track
export graph, the multi-row page, and MCP over the same shape. A version-1
project file (from before multi-track) opens and migrates automatically.
`python3 -m studio --help` lists every command.

## Preview note

iPhone HEVC clips play in Safari; Chrome on a Mac may not decode them. That is preview only. Export goes through ffmpeg, which decodes HEVC.
