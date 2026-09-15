"""Sightbox Studio: a simple timeline video editor with one write path for a person and an agent.

The cut list is the editor's whole state. The page is a view over it, and every change,
whoever makes it, is a small JSON object applied by `studio.project`. `studio.media`
wraps ffmpeg and ffprobe. `studio.server` serves the page and the local API.
`studio.mcp` exposes the same operations to an agent as MCP tools.
"""

__version__ = "0.1.0"
