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
        cut_changes = [{"op": "add_cut", "source": r["source"]} for r in results]
        store.apply(cut_changes, by="cli", probe=media.probe)


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
    tl = project.timeline()
    if not tl:
        print("(no cuts)")
        return
    print(f"{'cut':<5}{'source':<8}{'in':>9}{'out':>9}{'start':>9}{'end':>9}")
    for e in tl:
        print(f"{e['cut']:<5}{e['source']:<8}{e['in']:>9.3f}{e['out']:>9.3f}{e['start']:>9.3f}{e['end']:>9.3f}")


def cmd_export(args):
    store = Store(args.project)
    project = store.load()
    out = args.out or os.path.join(store.dir, f"{slugify(project.name)}.mp4")
    out = os.path.abspath(os.path.expanduser(out))
    result = media.export(project, store.resolve, out, args.preset)
    print(f"{result['path']}  {result['bytes']} bytes  {result['duration']} s  ({result['seconds']} s to export)")


def cmd_serve(args):
    run_server(args.project, port=args.port, media_dir=args.media)


def cmd_probe(args):
    print(json.dumps(media.probe(args.file), indent=2))


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
