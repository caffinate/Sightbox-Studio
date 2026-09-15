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

Build orders 1 to 3 are in the repository and tested: the cut list and the write
path (`studio/project.py`), media and the server (`studio/media.py`,
`studio/server.py`, `studio/__main__.py`), and the page (`app/studio.html`).
Build orders 4 and 5 (agent eyes, MCP) are specified in the brief and not yet
built.

The page is styled with the Sightbox Design System (charcoal canvas, white
ink, cyan accent, hairline rings, tracked mono labels) rather than the token
appendix in `BRIEF.md`, at the person's direction. It runs on system font
stacks only — no webfont `<link>` — so it never depends on network access to
render or to pass a console-error check; that's also the design system's own
documented fallback for its two commercial faces (Neue Haas Grotesk, NB
Architekt Std).

## Preview note

iPhone HEVC clips play in Safari; Chrome on a Mac may not decode them. That is preview only. Export goes through ffmpeg, which decodes HEVC.
