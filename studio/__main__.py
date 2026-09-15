"""The CLI. Every write goes through `studio.project.Store`, attributed `by="cli"`.

    python3 -m studio new PROJECT.json [--name NAME]
    python3 -m studio add PROJECT.json FILE...          add_source for each; --cut also appends a whole cut
    python3 -m studio apply PROJECT.json [JSON | -]     a batch (a JSON list of changes) from the argument or stdin
    python3 -m studio show PROJECT.json                 the timeline as a table
    python3 -m studio export PROJECT.json [OUT.mp4] [--preset medium]
    python3 -m studio serve PROJECT.json [--port 3200] [--media DIR]
    python3 -m studio probe FILE                        what ffprobe says, as the source fields
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import media
from .project import ChangeError, Store

BY = "cli"


def _probe_or_none():
    return media.probe if media.available() else None


def cmd_new(args: argparse.Namespace) -> int:
    store = Store(args.project)
    try:
        store.create(args.name or "Untitled")
    except FileExistsError:
        print(f"{store.path} already exists", file=sys.stderr)
        return 1
    print(store.path)
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    if not media.available():
        print("ffmpeg and ffprobe are needed on PATH to add a source", file=sys.stderr)
        return 1
    store = Store(args.project)
    for path in args.files:
        try:
            _, results = store.apply([{"op": "add_source", "path": path}], BY, probe=media.probe)
            sid = results[0]["source"]
            if args.cut:
                store.apply([{"op": "add_cut", "source": sid}], BY, probe=media.probe)
            print(f"{sid}\t{path}")
        except ChangeError as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            return 1
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    raw = sys.stdin.read() if args.json == "-" else args.json
    try:
        changes = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"malformed JSON: {exc}", file=sys.stderr)
        return 1
    store = Store(args.project)
    try:
        _, results = store.apply(changes, BY, probe=_probe_or_none())
    except ChangeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(results, indent=2))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    project = Store(args.project).load()
    rows = project.timeline()
    if not rows:
        print("(no cuts)")
        return 0
    widths = {"cut": 4, "source": 6, "in": 7, "out": 7, "start": 7, "end": 7}
    header = f"{'cut':<{widths['cut']}} {'source':<{widths['source']}} {'in':>{widths['in']}} {'out':>{widths['out']}} {'start':>{widths['start']}} {'end':>{widths['end']}}"
    print(header)
    for r in rows:
        print(f"{r['cut']:<{widths['cut']}} {r['source']:<{widths['source']}} {r['in']:>{widths['in']}.3f} {r['out']:>{widths['out']}.3f} {r['start']:>{widths['start']}.3f} {r['end']:>{widths['end']}.3f}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    if not media.available():
        print("ffmpeg and ffprobe are needed on PATH to export", file=sys.stderr)
        return 1
    store = Store(args.project)
    project = store.load()
    out_path = args.out or store.resolve(f"{media.slug(project.name)}.mp4")
    t0 = time.time()
    try:
        result = media.export(project, store.resolve, out_path, args.preset)
    except media.MediaError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    seconds = round(time.time() - t0, 3)
    print(f"{out_path}\t{result['bytes']} bytes\t{seconds}s\t{result['duration']}s")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from . import server
    server.serve(args.project, port=args.port, media_dir=args.media)
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    if not media.available():
        print("ffmpeg and ffprobe are needed on PATH to probe", file=sys.stderr)
        return 1
    try:
        info = media.probe(args.file)
    except media.MediaError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(info, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m studio")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("new")
    p.add_argument("project")
    p.add_argument("--name")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("add")
    p.add_argument("project")
    p.add_argument("files", nargs="+")
    p.add_argument("--cut", action="store_true")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("apply")
    p.add_argument("project")
    p.add_argument("json")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("show")
    p.add_argument("project")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("export")
    p.add_argument("project")
    p.add_argument("out", nargs="?")
    p.add_argument("--preset", default="medium")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("serve")
    p.add_argument("project")
    p.add_argument("--port", type=int, default=3200)
    p.add_argument("--media")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("probe")
    p.add_argument("file")
    p.set_defaults(func=cmd_probe)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
