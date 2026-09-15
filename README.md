# Sightbox Studio

A simple timeline video editor with one write path for a person and an agent. One track, cuts only. The cut list is a JSON file; a page in the browser is a view over it; a local Python server applies changes, serves the media and runs ffmpeg; an agent changes it the same way, through the same server, and sees the footage through ffmpeg: frames, contact sheets, scene changes, silences. Export is a real H.264 MP4.

The build brief is `BRIEF.md`. Read it before building. The boundaries are in `AGENTS.md`.

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
scenes, silences), and MCP. `python3 -m studio --help` lists every command.

## Preview note

iPhone HEVC clips play in Safari; Chrome on a Mac may not decode them. That is preview only. Export goes through ffmpeg, which decodes HEVC.
