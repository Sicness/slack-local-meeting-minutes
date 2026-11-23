"""`ma ui`: web UI on 127.0.0.1 with recordings, meeting voices and playable clips, names, merging.

Local only. Other sites in the browser can send requests to 127.0.0.1, so Host must be ours
(DNS rebinding protection) and actions (POST) need an X-MA header, which a foreign page cannot set
without CORS permission.
"""

import fcntl
import hmac
import io
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, diarize, forget, i18n, log, meta as M, notes, paths, registry, voices_cli
from .process import rerender
from .transcript import clock

HERE = Path(__file__).parent
DECISIONS = ("matched", "new", "split", "short", "short-near", "unknown", "manual")
DATE_FORMAT = {"en": "%b %d, %H:%M", "ru": "%d.%m %H:%M"}


def cookie_name(port: int) -> str:
    return f"ma_session_{port}"   # cookies are shared between ports of one host


def ui_token() -> str:
    """Session secret in a 600 file: the menu bar app reads it to open the UI."""
    path = paths.home() / "ui.token"
    token = path.read_text().strip() if path.exists() else ""
    if not token:
        path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            f.write(token + "\n")
    return token


def _same(a: str | None, b: str) -> bool:
    return a is not None and secrets.compare_digest(a.encode(), b.encode())


def _meta(folder: Path) -> dict:
    try:
        return M.read(folder)
    except (OSError, json.JSONDecodeError):
        return {}


def _json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def busy(folder: Path) -> bool:
    """Another ma is processing the recording (.ma.lock)."""
    lock = folder / ".ma.lock"
    if not lock.exists():
        return False
    with open(lock) as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True


def obsidian_url(cfg: config.Config, rel: str | None) -> str | None:
    return f"obsidian://open?path={quote(str(Path(cfg.obsidian_vault) / rel))}" if rel else None


def summary(folder: Path, cfg: config.Config, reg: registry.Registry) -> dict:
    m = _meta(folder)
    tr = _json(folder / "transcript.json") or {}
    mn = _json(folder / "minutes.json")
    steps = {s: M.step(m, s).get("status", "") for s in M.STEPS}
    failed = next((s for s, st in steps.items() if st == "failed"), None)
    if busy(folder) or "running" in steps.values():
        state, kind = i18n.t(cfg.language, "state_busy"), "wait"
    elif failed:
        state, kind = i18n.t(cfg.language, "state_failed", step=failed), "bad"
    elif m.get("status") == "recording":
        state, kind = i18n.t(cfg.language, "state_recording"), "wait"
    elif all(st == "done" for st in steps.values()):
        state, kind = i18n.t(cfg.language, "state_done"), "ok"
    else:
        state, kind = i18n.t(cfg.language, "state_wait"), "wait"
    people = [cfg.me_name]
    for k, v in tr.get("speakers", {}).items():
        if k.startswith("@v"):
            try:
                people.append(reg.display(int(k[2:])))   # registry name, even if notes are not re-rendered yet
            except (registry.RegistryError, ValueError):
                people.append(v)
    note = (m.get("notes") or {})
    return {"id": folder.name, "date": notes.started(m, folder).strftime(DATE_FORMAT[cfg.language]),
            "duration": clock(float(m.get("duration_seconds") or 0)),
            "title": (mn or {}).get("minutes", {}).get("title") or "", "people": people, "state": state, "state_kind": kind,
            "failed": failed, "error": M.step(m, failed).get("error") if failed else None, "steps": steps,
            "meeting_url": obsidian_url(cfg, note.get("meeting")), "transcript_url": obsidian_url(cfg, note.get("transcript"))}


def voice_options(reg: registry.Registry) -> list[dict]:
    return [{"id": v["id"], "label": v["name"] or v["label"]} for v in reg.list_voices()]


def create_app(allowed_hosts: set[str] | None = None) -> FastAPI:
    cfg = config.load()
    port = cfg.ui_port
    hosts = allowed_hosts or {f"127.0.0.1:{port}", f"localhost:{port}"}
    token, cookie = ui_token(), cookie_name(port)
    app = FastAPI(title="Meeting Assistant", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    _t = lambda key, **values: i18n.t(cfg.language, key, **values)
    templates.env.globals.update(t=_t, lang=cfg.language, browser_strings=i18n.browser_strings(cfg.language),
                                 retention_days=f"{cfg.audio_retention_days:g}")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host", "") not in hosts:
            return PlainTextResponse("unexpected Host", status_code=403)
        if request.url.path == "/ma-identity":   # proves we know the token without revealing it
            nonce = request.query_params.get("nonce", "")
            if not 16 <= len(nonce) <= 128:
                return PlainTextResponse("bad nonce", status_code=400)
            return PlainTextResponse(hmac.new(token.encode(), nonce.encode(), "sha256").hexdigest())
        given = request.query_params.get("token")
        if given is not None and request.method == "GET" and _same(given, token):
            rest = urlencode([(k, v) for k, v in request.query_params.multi_items() if k != "token"])
            target = "/" + request.url.path.lstrip("/") + (f"?{rest}" if rest else "")
            response = RedirectResponse(target, status_code=303)
            response.set_cookie(cookie, token, httponly=True, samesite="strict", path="/")
            return response
        if not _same(request.cookies.get(cookie), token):
            return PlainTextResponse("Open this page from the menu bar app or with `ma ui`.", status_code=401)
        if request.method != "GET":
            origin = request.headers.get("origin")
            if request.headers.get("x-ma") != "1" or (origin and origin.split("//", 1)[-1] not in hosts):
                return PlainTextResponse("request did not come from the UI", status_code=403)
        return await call_next(request)

    def folder_of(rid: str) -> Path:
        folder = paths.recordings() / rid
        if "/" in rid or rid.startswith(".") or not (folder / "meta.json").exists():
            raise HTTPException(404, _t("err_no_recording"))
        return folder

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        root = paths.recordings()
        folders = sorted((p for p in root.iterdir() if (p / "meta.json").exists()), reverse=True) if root.exists() else []
        with registry.Registry() as reg:
            rows = [summary(f, cfg, reg) for f in folders]
        return templates.TemplateResponse(request, "index.html", {"rows": rows, "page": "index"})

    @app.get("/r/{rid}", response_class=HTMLResponse)
    def recording(request: Request, rid: str):
        folder = folder_of(rid)
        v = _json(folder / "voices.json") or {"clusters": []}
        tr = _json(folder / "transcript.json") or {"utterances": [], "speakers": {}}
        have_audio = (folder / "remote.16k.wav").exists() or (folder / "audio.m4a").exists()
        with registry.Registry() as reg:
            info = summary(folder, cfg, reg)
            voices = []
            for c in sorted(v["clusters"], key=lambda c: -c["speech_seconds"]):
                vid = reg.resolve(c["voice_id"]) if c["voice_id"] is not None else None
                rec = reg.voice(vid) if vid else None
                said = [u["text"] for u in tr["utterances"] if vid and u["speaker"] == f"@v{vid}" and len(u["text"]) > 20][:3]
                voices.append({"cluster": c["cluster"], "voice_id": vid, "label": rec["label"] if rec else _t("unknown"),
                               "name": rec["name"] if rec else None, "speech": clock(c["speech_seconds"]),
                               "decision": _t(f"decision_{c['decision']}") if c["decision"] in DECISIONS else c["decision"], "similarity": c.get("similarity"),
                               "clips": [{"start": a, "end": b, "at": clock(a)} for a, b in c.get("clips", [])],
                               "said": [s if len(s) <= 160 else s[:157] + "…" for s in said],
                               "meetings": len(reg.recordings_of(vid)) if vid else 0})
            options = voice_options(reg)
        return templates.TemplateResponse(request, "recording.html", {
            "r": info, "voices": voices, "options": options, "have_audio": have_audio, "steps": M.STEPS, "page": "recording"})

    @app.get("/voices", response_class=HTMLResponse)
    def voices_page(request: Request):
        with registry.Registry() as reg:
            rows = []
            for v in reg.list_voices():
                samples = reg.samples(v["id"])
                last = samples[-1] if samples else None
                clip = last["clips"][0] if last and last["clips"] else None
                rows.append({**v, "speech": clock(v["speech_seconds"]), "recordings": reg.recordings_of(v["id"]),
                             "clip": {"rid": last["recording_id"], "start": clip[0], "end": clip[1]} if clip else None})
            options = voice_options(reg)
        return templates.TemplateResponse(request, "voices.html", {"rows": rows, "options": options, "page": "voices"})

    @app.get("/r/{rid}/clip")
    def clip(rid: str, start: float, end: float):
        """Clip of the remote track (WAV): from remote.16k.wav, or from audio.m4a (right channel) once wavs are gone."""
        folder = folder_of(rid)
        start, end = max(0.0, start), max(start + 0.2, min(end, start + 30))
        wav = folder / "remote.16k.wav"
        buf = io.BytesIO()
        if wav.exists():
            info = sf.info(str(wav))
            x, sr = sf.read(str(wav), start=int(start * info.samplerate), stop=int(end * info.samplerate), dtype="int16")
            sf.write(buf, x, sr, format="WAV", subtype="PCM_16")
        elif (folder / "audio.m4a").exists():
            p = subprocess.run([cfg.ffmpeg, "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
                                "-i", str(folder / "audio.m4a"), "-af", "pan=mono|c0=c1", "-f", "wav", "-"],
                               capture_output=True, check=False)
            if p.returncode != 0:
                raise HTTPException(500, _t("err_cut"))
            buf.write(p.stdout)
        else:
            raise HTTPException(404, _t("err_audio_gone", days=f"{cfg.audio_retention_days:g}"))
        return Response(buf.getvalue(), media_type="audio/wav", headers={"Cache-Control": "max-age=3600"})

    # --- actions ---

    @app.post("/api/voices/{vid}/name")
    async def set_name(vid: int, request: Request):
        name = str((await request.json()).get("name", "")).strip()
        with registry.Registry() as reg:
            vid = reg.set_name(vid, name or None)
            done, skipped = voices_cli.rerender_voice(reg, vid)
        return {"voice_id": vid, "rerendered": done, "skipped": skipped}

    @app.post("/api/voices/merge")
    async def merge(request: Request):
        body = await request.json()
        with registry.Registry() as reg:
            vid = reg.merge(int(body["into"]), int(body["other"]))
            done, skipped = voices_cli.rerender_voice(reg, vid)
        return {"voice_id": vid, "rerendered": done, "skipped": skipped}

    @app.post("/api/r/{rid}/reassign")
    async def reassign(rid: str, request: Request):
        folder = folder_of(rid)
        body = await request.json()
        cluster, target = str(body["cluster"]), body.get("voice_id")
        v = _json(folder / "voices.json") or {"clusters": []}
        c = next((c for c in v["clusters"] if c["cluster"] == cluster), None)
        if c is None:
            raise HTTPException(404, _t("err_no_voice"))
        with registry.Registry() as reg:
            vid = reg.reassign(rid, cluster, int(target) if target not in (None, "", "new") else None,
                               np.asarray(c.get("centroid") or [], dtype=float) if c.get("centroid") else None)
            diarize.set_cluster_voice(folder, cluster, vid)
            ok = rerender(folder, reg)
        log.info(f"ui: {rid} {cluster} → voice {vid}")
        return {"voice_id": vid, "rerendered": ok}

    @app.post("/api/voices/{vid}/delete")
    def delete_voice(vid: int):
        try:
            return forget.delete_voice(vid)
        except forget.ForgetError:
            raise HTTPException(409, _t("err_busy")) from None
        except registry.RegistryError:
            raise HTTPException(404, _t("err_no_voice")) from None

    @app.post("/api/r/{rid}/delete")
    def delete_recording(rid: str):
        folder = folder_of(rid)
        try:
            return forget.delete_recording(folder.name)
        except forget.ForgetError:
            raise HTTPException(409, _t("err_busy")) from None

    @app.post("/api/r/{rid}/rerender")
    def rerender_one(rid: str):
        folder = folder_of(rid)
        with registry.Registry() as reg:
            ok = rerender(folder, reg)
        if not ok:
            raise HTTPException(409, _t("err_not_ready"))
        return {"rerendered": True}

    @app.post("/api/r/{rid}/process")
    async def process_again(rid: str, request: Request):
        folder = folder_of(rid)
        step = (await request.json()).get("from") or None
        if step and step not in M.STEPS:
            raise HTTPException(400, _t("err_no_step"))
        if busy(folder):
            raise HTTPException(409, _t("err_busy"))
        paths.logs().mkdir(parents=True, exist_ok=True)
        out = open(paths.logs() / "process.log", "a")
        cmd = [sys.executable, "-m", "ma.cli", "process", rid] + (["--from", step] if step else [])
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        log.info(f"ui: {rid}: reprocessing" + (f" from step {step}" if step else ""))
        return JSONResponse({"started": True}, status_code=202)

    return app


def serve() -> int:
    import uvicorn
    cfg = config.load()
    log.info(f"ui: http://127.0.0.1:{cfg.ui_port}")
    if sys.stdout.isatty():   # never in a log file
        print(f"Open http://127.0.0.1:{cfg.ui_port}/?token={ui_token()}")
    uvicorn.run(create_app(), host="127.0.0.1", port=cfg.ui_port, log_level="warning")
    return 0
