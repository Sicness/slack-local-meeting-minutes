"""The `ma` CLI. From the repository: `uv run --project pipeline ma …`."""

import argparse
import sys
import time

from . import config, doctor, forget, log, meta as M, paths, priority, registry, voices_cli
from .process import ProcessError, process, rerender
from .transcript import clock


def cmd_process(args) -> int:
    folder = paths.recording(args.recording)
    mode = priority.apply(args.priority or config.load().priority)
    log.info(f"{folder.name}: priority {mode}")
    t = time.time()
    try:
        process(folder, force=args.force, from_step=args.from_step)
    except ProcessError as e:
        log.error(f"{folder.name}: {e}")
        return 1
    except Exception:
        return 1
    log.info(f"{folder.name}: all done in {time.time() - t:.1f} s → {folder / 'transcript.md'}")
    return 0


def cmd_rerender(args) -> int:
    root = paths.recordings()
    folders = sorted(p for p in root.iterdir() if (p / "meta.json").exists()) if args.recording == "all" \
        else [paths.recording(args.recording)]
    with registry.Registry() as reg:
        for folder in folders:
            ok = rerender(folder, reg)
            log.info(f"{folder.name}: " + ("re-rendered" if ok else "skipped: not processed or busy"))
    return 0


def cmd_cleanup(args) -> int:
    from . import cleanup
    days = args.days if args.days is not None else config.load().audio_retention_days
    cleanup.run(days, dry_run=args.dry_run)
    return 0


def cmd_delete(args) -> int:
    folder = forget.recording_folder(args.recording)
    what = "audio, transcript and voice data" + ("" if args.keep_notes else " and Obsidian notes")
    if not forget.confirm(f"Permanently delete {what} of {folder.name}?", args.yes):
        print("cancelled")
        return 1
    res = forget.delete_recording(folder.name, keep_notes=args.keep_notes)
    print(f"deleted {res['recording']}; voices erased: {len(res['voices_removed'])}")
    for rel, outcome in res["notes"].items():
        print(f"  note {rel}: {outcome}")
    return 0


def cmd_doctor(args) -> int:
    return doctor.run()


def cmd_init(args) -> int:
    try:
        path = doctor.init(args.name, args.language, args.vault, args.uv, args.pipeline)
    except (FileExistsError, ValueError) as e:
        log.error(str(e))
        return 1
    print(f"created {path}")
    return 0


def cmd_ui(args) -> int:
    from . import web
    return web.serve()


def cmd_list(args) -> int:
    root = paths.recordings()
    if not root.exists():
        print(f"no recordings ({root})")
        return 0
    for folder in sorted(p for p in root.iterdir() if (p / "meta.json").exists()):
        m = M.read(folder)
        steps = " ".join(f"{s}:{M.step(m, s).get('status', '—')}" for s in M.STEPS)
        print(f"{folder.name}  {m.get('status', '?'):9}  {clock(float(m.get('duration_seconds') or 0))}  "
              f"{m.get('mode', '?'):10}  {steps}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ma", description="Process call recordings")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("process", help="prepare, transcribe, identify voices, write minutes and Obsidian notes")
    p.add_argument("recording", help="recording id or folder path")
    p.add_argument("--force", action="store_true", help="rerun all steps")
    p.add_argument("--from", dest="from_step", choices=M.STEPS, help="rerun from this step")
    p.add_argument("--priority", choices=priority.MODES, help="default from config.toml, otherwise background")
    p.set_defaults(func=cmd_process)
    rp = sub.add_parser("rerender", help="rebuild the transcript and Obsidian notes without models (voice names)")
    rp.add_argument("recording", help="recording id, path or all")
    rp.set_defaults(func=cmd_rerender)
    cp = sub.add_parser("cleanup", help="delete audio of processed recordings older than audio_retention_days (7 days)")
    cp.add_argument("--dry-run", action="store_true", help="only show what would be deleted")
    cp.add_argument("--days", type=float, help="age threshold in days (default from config.toml)")
    cp.set_defaults(func=cmd_cleanup)
    dp = sub.add_parser("delete", help="permanently delete a recording, its voice data and notes")
    dp.add_argument("recording", help="recording id")
    dp.add_argument("--keep-notes", action="store_true", help="keep the Obsidian notes")
    dp.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    dp.set_defaults(func=cmd_delete)
    sub.add_parser("doctor", help="check the setup and show how to fix problems").set_defaults(func=cmd_doctor)
    ip = sub.add_parser("init", help="create config.toml (never overwrites it)")
    ip.add_argument("--name", help="your name in notes")
    ip.add_argument("--language", default="en", help="interface and notes: en or ru")
    ip.add_argument("--vault", help="Obsidian vault folder")
    ip.add_argument("--uv", help="path to uv for the recorder")
    ip.add_argument("--pipeline", help="path to pipeline/ for the recorder")
    ip.set_defaults(func=cmd_init)
    up = sub.add_parser("ui", help="web UI on 127.0.0.1 ([ui] port, default 8765)")
    up.set_defaults(func=cmd_ui)
    lp = sub.add_parser("list", help="recordings and step statuses")
    lp.set_defaults(func=cmd_list)
    voices_cli.add_parser(sub)
    args = ap.parse_args(argv)
    paths.secure_home()
    try:
        return args.func(args)
    except (registry.RegistryError, forget.ForgetError) as e:
        log.error(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
