"""`ma voices …`: list registry voices, set names, merge. Transcripts are rebuilt after name/merge."""

from . import forget, log, paths, registry
from .process import rerender
from .transcript import clock


def rerender_voice(reg: registry.Registry, voice_id: int) -> tuple[int, list[str]]:
    """Rebuild transcripts and notes of all the voice's meetings. → (rebuilt count, skipped ids)."""
    done, skipped = 0, []
    for rid in reg.recordings_of(voice_id):
        folder = paths.recordings() / rid
        if (folder / "meta.json").exists() and rerender(folder, reg):
            done += 1
        else:
            skipped.append(rid)
    log.info(f"transcripts rebuilt: {done}" + (f"; skipped (not ready or busy): {', '.join(skipped)}" if skipped else ""))
    return done, skipped


_rerender = rerender_voice


def cmd_list(args) -> int:
    with registry.Registry() as reg:
        rows = reg.list_voices(include_empty=args.all)
    if not rows:
        print("no voices yet")
        return 0
    print(f"{'id':>4}  {'voice':12} {'name':24} {'meets':>6} {'speech':>8}  last")
    for v in rows:
        print(f"{v['id']:>4}  {v['label']:12} {(v['name'] or '—'):24} {v['meetings']:>6} "
              f"{clock(v['speech_seconds']):>8}  {v['last_recording'] or '—'}")
    return 0


def cmd_show(args) -> int:
    with registry.Registry() as reg:
        vid = reg.resolve(args.id)
        v = reg.voice(vid)
        print(f"{v['label']}" + (f" — {v['name']}" if v["name"] else "") + (f" (voice {args.id} merged here)" if vid != args.id else ""))
        for s in reg.samples(vid):
            clips = ", ".join(f"{clock(a)}–{clock(b)}" for a, b in s["clips"])
            print(f"  {s['recording_id']}  {s['cluster']:4} speech {clock(s['speech_seconds'])}  clips: {clips}")
    return 0


def cmd_name(args) -> int:
    name = " ".join(args.name).strip()
    with registry.Registry() as reg:
        vid = reg.set_name(args.id, name or None)
        log.info(f"{reg.voice(vid)['label']}: " + ("name set" if name else "name cleared"))
        _rerender(reg, vid)
    return 0


def cmd_merge(args) -> int:
    with registry.Registry() as reg:
        vid = reg.merge(args.into, args.other)
        log.info(f"voice {args.other} merged into {reg.voice(vid)['label']}")
        _rerender(reg, vid)
    return 0


def cmd_delete(args) -> int:
    with registry.Registry() as reg:
        vid = reg.resolve(args.id)
        v = reg.voice(vid)
        title = v["name"] or v["label"]
        count = len(reg.recordings_of(vid))
    if not forget.confirm(f"Erase the voiceprint of {title} and unlink it from {count} recordings?", args.yes):
        print("cancelled")
        return 1
    res = forget.delete_voice(vid)
    print(f"deleted {title}: {res['recordings']} recordings updated" +
          (f", not re-rendered: {', '.join(res['skipped'])}" if res["skipped"] else ""))
    return 0


def add_parser(sub) -> None:
    vp = sub.add_parser("voices", help="voices across meetings: list, names, merging")
    vs = vp.add_subparsers(dest="voices_cmd", required=True)
    p = vs.add_parser("list", help="registry voices")
    p.add_argument("--all", action="store_true", help="include voices with no meetings")
    p.set_defaults(func=cmd_list)
    p = vs.add_parser("show", help="a voice's meetings and playable clips")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_show)
    p = vs.add_parser("name", help="set a name (empty clears it)")
    p.add_argument("id", type=int)
    p.add_argument("name", nargs="*")
    p.set_defaults(func=cmd_name)
    p = vs.add_parser("merge", help="same person: voice OTHER moves into INTO")
    p.add_argument("into", type=int)
    p.add_argument("other", type=int)
    p.set_defaults(func=cmd_merge)
    p = vs.add_parser("delete", help="erase a voiceprint and name; the speech stays as Unknown")
    p.add_argument("id", type=int)
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.set_defaults(func=cmd_delete)
