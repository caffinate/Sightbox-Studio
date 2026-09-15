"""The command line: `python3 -m studio <command> ...`. Goes through the same
`Store.apply` as the page, curl and MCP; a CLI write is attributed `by "cli"`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import media
from .project import ChangeError, Store
from .server import serve as run_server
from .server import slugify


def cmd_new(args):
    store = Store(args.project)
    try:
        store.create(args.name or "Untitled")
    except FileExistsError:
        sys.exit(f"already exists: {store.path}")
    print(store.path)


def cmd_add(args):
    store = Store(args.project)
    changes = [{"op": "add_source", "path": f} for f in args.files]
    project, results = store.apply(changes, by="cli", probe=media.probe)
    for f, r in zip(args.files, results):
        print(f"{r['source']}{' (already added)' if r.get('existing') else ''}  {f}")
    if args.cut:
        clip_changes = [{"op": "add_clip", "source": r["source"]} for r in results]
        store.apply(clip_changes, by="cli", probe=media.probe)


def cmd_apply(args):
    store = Store(args.project)
    raw = sys.stdin.read() if args.json in (None, "-") else args.json
    payload = json.loads(raw)
    changes = payload["changes"] if isinstance(payload, dict) and "changes" in payload else payload
    project, results = store.apply(changes, by="cli", probe=media.probe)
    print(json.dumps(results, indent=2))


def cmd_show(args):
    store = Store(args.project)
    project = store.load()
    segments = project.video_segments()
    if not segments:
        print("(no clips)")
        return
    print("Picture (resolved, what actually plays):")
    print(f"  {'start':>8}{'end':>8}  {'source':<8}{'in':>9}{'out':>9}")
    for s in segments:
        if s["kind"] == "clip":
            print(f"  {s['start']:>8.3f}{s['end']:>8.3f}  {s['source']:<8}{s['in']:>9.3f}{s['out']:>9.3f}")
        else:
            print(f"  {s['start']:>8.3f}{s['end']:>8.3f}  (black)")
    print("\nClips by track:")
    for t in project.tracks:
        clips = sorted(project.clips_on(t["id"]), key=lambda c: c["start"])
        if not clips:
            continue
        print(f"  {t['id']} ({t['kind']}):")
        for c in clips:
            link = f"  link={c['link']}" if c.get("link") else ""
            print(f"    {c['id']:<5}{c['source']:<8}{c['in']:>8.3f}{c['out']:>8.3f}{c['start']:>8.3f}{link}")


def cmd_export(args):
    store = Store(args.project)
    project = store.load()
    out = args.out or os.path.join(store.dir, f"{slugify(project.name)}.mp4")
    out = os.path.abspath(os.path.expanduser(out))
    result = media.export(project, store.resolve, out, args.preset)
    print(f"{result['path']}  {result['bytes']} bytes  {result['duration']} s  ({result['seconds']} s to export)")


def cmd_serve(args):
    run_server(args.project, port=args.port, media_dir=args.media)


def cmd_mcp(args):
    from . import mcp
    mcp.serve(args.project)


def cmd_probe(args):
    print(json.dumps(media.probe(args.file), indent=2))


def cmd_frame(args):
    store = Store(args.project)
    source = store.load().source(args.source)
    data = media.frame(store.resolve(source["path"]), args.t, args.width)
    out = args.out or f"{args.source}-{args.t:g}.jpg"
    with open(out, "wb") as fh:
        fh.write(data)
    print(out)


def cmd_sheet(args):
    store = Store(args.project)
    source = store.load().source(args.source)
    data, interval, cols, rows = media.sheet(store.resolve(source["path"]), source["duration"], args.cols, args.rows)
    out = args.out or f"{args.source}-sheet.jpg"
    with open(out, "wb") as fh:
        fh.write(data)
    print(f"{out}  {cols}x{rows} tiles every {interval:.3f}s")


def cmd_scenes(args):
    store = Store(args.project)
    source = store.load().source(args.source)
    found = media.scenes(store.resolve(source["path"]), args.threshold)
    print(json.dumps(found, indent=2))


def cmd_silences(args):
    store = Store(args.project)
    source = store.load().source(args.source)
    found = media.silences(store.resolve(source["path"]), source.get("has_audio", False),
                            args.noise, args.min, duration=source["duration"])
    print(json.dumps(found, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="studio")
    sub = p.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="create a project")
    new.add_argument("project")
    new.add_argument("--name")
    new.set_defaults(func=cmd_new)

    add = sub.add_parser("add", help="add_source for each file")
    add.add_argument("project")
    add.add_argument("files", nargs="+")
    add.add_argument("--cut", action="store_true", help="also append a whole cut of each")
    add.set_defaults(func=cmd_add)

    apply_ = sub.add_parser("apply", help="apply a batch from the argument or stdin")
    apply_.add_argument("project")
    apply_.add_argument("json", nargs="?", default="-")
    apply_.set_defaults(func=cmd_apply)

    show = sub.add_parser("show", help="the timeline as a table")
    show.add_argument("project")
    show.set_defaults(func=cmd_show)

    export = sub.add_parser("export", help="export the cut list to an MP4")
    export.add_argument("project")
    export.add_argument("out", nargs="?")
    export.add_argument("--preset", default="medium")
    export.set_defaults(func=cmd_export)

    serve = sub.add_parser("serve", help="run the local server")
    serve.add_argument("project")
    serve.add_argument("--port", type=int, default=3200)
    serve.add_argument("--media", help="the folder /api/media lists; default the project's own folder")
    serve.set_defaults(func=cmd_serve)

    probe = sub.add_parser("probe", help="what ffprobe says, as the source fields")
    probe.add_argument("file")
    probe.set_defaults(func=cmd_probe)

    frame = sub.add_parser("frame", help="a JPEG frame at a source time")
    frame.add_argument("project")
    frame.add_argument("source")
    frame.add_argument("t", type=float)
    frame.add_argument("--width", type=int, default=640)
    frame.add_argument("-o", "--out", dest="out")
    frame.set_defaults(func=cmd_frame)

    sheet = sub.add_parser("sheet", help="a contact sheet JPEG")
    sheet.add_argument("project")
    sheet.add_argument("source")
    sheet.add_argument("--cols", type=int, default=6)
    sheet.add_argument("--rows", type=int, default=5)
    sheet.add_argument("-o", "--out", dest="out")
    sheet.set_defaults(func=cmd_sheet)

    scenes = sub.add_parser("scenes", help="scene changes as {t, score}")
    scenes.add_argument("project")
    scenes.add_argument("source")
    scenes.add_argument("--threshold", type=float, default=0.3)
    scenes.set_defaults(func=cmd_scenes)

    silences = sub.add_parser("silences", help="silent spans as {start, end}")
    silences.add_argument("project")
    silences.add_argument("source")
    silences.add_argument("--noise", type=float, default=-30)
    silences.add_argument("--min", type=float, default=0.5)
    silences.set_defaults(func=cmd_silences)

    mcp = sub.add_parser("mcp", help="the MCP server on stdio")
    mcp.add_argument("project")
    mcp.set_defaults(func=cmd_mcp)

    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except ChangeError as e:
        sys.exit(str(e))
    except FileNotFoundError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
