"""asr step: mlx-whisper over each whole track, glossary prompt in every window, VAD filter.

Why:
- a whole track yields the least invented text; cutting by VAD yields more;
- with condition_on_previous_text=False mlx-whisper drops initial_prompt after the first window,
  so the prompt is injected into every window (DecodingOptions is patched);
- the filter removes the remaining hallucinations ("To be continued...", text on noise,
  mixed-language garbage, prompt echo).
"""

import json
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from . import log, vad
from .config import Config
from .glossary import Glossary

HALLUCINATIONS = [re.compile(p) for p in [
    r"продолжение следует",
    r"субтитры (сделал|делал|создавал|подготовил|подогнал|делала).*",
    r"редактор субтитров.*",
    r"корректор.*",
    r"спасибо за просмотр.*",
    r"подписывайтесь( на (наш )?канал)?.*",
    r"ставьте лайк.*",
    r"(thank you|thanks) for watching.*",
    r"(please )?(like and )?subscribe( to (my|our|the) channel)?",
    r"subtitles by.*",
    r"see you in the next video",
]]

MIN_SPEECH_SHARE = 0.3     # share of a segment that must be speech per VAD
LOGPROB_HARD = -1.5        # below this is garbage
LOGPROB_SOFT = -1.0        # below this is garbage if VAD speech covers less than SOFT_SPEECH_SHARE
SOFT_SPEECH_SHARE = 0.6

RESCUE_MIN_SPEECH = 3.0    # s of VAD speech under a failed segment: transcribe the span again
RESCUE_PAD = 0.5
# Second opinion: a second model (large-v3) retranscribes weak segments and the more confident
# variant wins. large-v3 is better on some weak spots and worse on others.
WEAK_LOGPROB = -0.45
WEAK_WORD_P = 0.65
SECOND_PAD = 0.3
SECOND_GAP = 1.0           # weak segments closer than this form one span
SECOND_MARGIN = 0.05       # how much higher the second model's avg_logprob must be
# Temperature fallback (Whisper defaults to 6 values) adds no words on real recordings and takes
# 2–6x longer on a noisy track. rescue catches the loops it used to fix.
MAIN_TEMPERATURES = (0.0,)
RESCUE_TEMPERATURES = (0.0, 0.4)

_carry: dict = {"on": False, "tokens": None}


def _norm(text: str) -> str:
    """Lowercase, no punctuation, single spaces."""
    return " ".join(re.sub(r"[^\w\s-]", " ", text.lower()).split())


@contextmanager
def _carry_prompt():
    """Prompt in every window: the first window gets initial_prompt; its tokens are remembered and
    injected into later windows, where mlx-whisper would pass an empty prompt."""
    import mlx_whisper  # noqa: F401
    T = sys.modules["mlx_whisper.transcribe"]
    if not getattr(T, "_ma_patched", False):
        original = T.DecodingOptions

        def patched(**kw):
            if _carry["on"]:
                p = list(kw.get("prompt") or [])
                c = _carry["tokens"]
                if c is None and p:
                    _carry["tokens"] = p
                elif c and p[: len(c)] != c:
                    kw["prompt"] = c + p[-(223 - len(c)):] if p else list(c)
            return original(**kw)

        T.DecodingOptions = patched
        T._ma_patched = True
    _carry.update(on=True, tokens=None)
    try:
        yield
    finally:
        _carry.update(on=False, tokens=None)


# exact versions the pipeline was tested with; a model set in config.toml uses its latest revision
REVISIONS = {
    "mlx-community/whisper-large-v3-turbo": "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb",
    "mlx-community/whisper-large-v3-mlx": "49e6aa286ad60c14352c404340ded53710378a11",
}


def model_path(repo: str) -> str:
    """Local model snapshot if already downloaded (works offline), otherwise download it."""
    from huggingface_hub import snapshot_download
    try:
        return snapshot_download(repo, revision=REVISIONS.get(repo), local_files_only=True)
    except Exception:
        log.info(f"asr: downloading model {repo} (1.6–3 GB, once)")
        return snapshot_download(repo, revision=REVISIONS.get(repo))


def token_counter(language: str):
    from mlx_whisper.tokenizer import get_tokenizer
    tok = get_tokenizer(multilingual=True, num_languages=100, language=language, task="transcribe")
    return lambda text: len(tok.encode(" " + text.strip()))


def filter_segments(segments: list[dict], regs: list[list[float]], prompt_words: set[str]) -> tuple[list[dict], list[dict]]:
    kept, dropped = [], []
    for s in segments:
        start, end = float(s["start"]), float(s["end"])
        text = s["text"].strip()
        norm = _norm(text)
        dur = max(0.1, end - start)
        share = vad.overlap(regs, start, end) / dur
        lp = float(s.get("avg_logprob", 0.0))
        words = [w for w in s.get("words", []) if vad.inside(regs, (w["start"] + w["end"]) / 2)]
        tokens = norm.split()
        why = None
        if not norm:
            why = "empty"
        elif "dimatorzok" in norm or any(h.fullmatch(norm) for h in HALLUCINATIONS):
            why = "known hallucination"
        elif share < MIN_SPEECH_SHARE:
            why = "outside speech"
        elif lp < LOGPROB_HARD or (lp < LOGPROB_SOFT and share < SOFT_SPEECH_SHARE):
            why = "low confidence"
        elif (len(tokens) >= 4 and sum(t in prompt_words for t in tokens) >= 0.6 * len(tokens)
              and text.count(",") >= (len(tokens) - 1) * 0.6):
            why = "prompt echo"
        elif not words:
            why = "words outside speech"
        info = {"start": round(start, 3), "end": round(end, 3), "text": text, "avg_logprob": round(lp, 3),
                "speech_share": round(share, 2)}
        if why:
            dropped.append({**info, "reason": why})
        else:
            info["words"] = [{"w": w["word"], "start": round(float(w["start"]), 3), "end": round(float(w["end"]), 3),
                              "p": round(float(w.get("probability", 0.0)), 3)} for w in words]
            kept.append(info)
    return kept, dropped


def is_loop(text: str) -> bool:
    """Whisper got stuck: one or two words repeated many times ("foo foo foo…")."""
    words = _norm(text).split()
    return len(words) >= 8 and len(set(words)) < 0.3 * len(words)


def needs_rescue(d: dict, regs: list[list[float]]) -> bool:
    if d["reason"] not in ("outside speech", "low confidence"):
        return False
    if not (is_loop(d["text"]) or d["avg_logprob"] < LOGPROB_SOFT):
        return False
    return vad.overlap(regs, d["start"], d["end"]) >= RESCUE_MIN_SPEECH


def decode_span(x, sr: int, a: float, b: float, model: str, cfg: Config, prompt: str | None,
                temperature=MAIN_TEMPERATURES) -> list[dict]:
    """Transcribe span [a, b] of a track; times are track seconds, words only inside the span."""
    import mlx_whisper

    a, b = max(0.0, a), min(len(x) / sr, b)
    with _carry_prompt():
        r = mlx_whisper.transcribe(x[int(a * sr):int(b * sr)], path_or_hf_repo=model, language=cfg.asr_language,
                                   word_timestamps=True, condition_on_previous_text=False,
                                   initial_prompt=prompt, temperature=temperature, verbose=None)
    for seg in r["segments"]:
        seg["start"] += a
        seg["end"] += a
        for w in seg.get("words", []):
            w["start"] += a
            w["end"] += a
        seg["words"] = [w for w in seg.get("words", []) if a <= (w["start"] + w["end"]) / 2 <= b]
    return r["segments"]


def mean_p(seg: dict) -> float:
    ps = [w["p"] for w in seg.get("words", [])]
    return sum(ps) / len(ps) if ps else 0.0


def is_weak(seg: dict) -> bool:
    return seg["avg_logprob"] < WEAK_LOGPROB or mean_p(seg) < WEAK_WORD_P


def weighted_logprob(segs: list[dict]) -> float:
    dur = [max(0.1, s["end"] - s["start"]) for s in segs]
    return sum(s["avg_logprob"] * d for s, d in zip(segs, dur)) / sum(dur)


def second_opinion(x, sr: int, kept: list[dict], model: str, cfg: Config, prompt: str,
                   regs: list[list[float]], prompt_words: set[str]) -> tuple[list[dict], int]:
    """Retranscribe weak segments (in groups) with the second model; keep the more confident variant."""
    groups: list[list[dict]] = []
    for seg in sorted(kept, key=lambda s: s["start"]):
        if is_weak(seg) and groups and is_weak(groups[-1][-1]) and seg["start"] - groups[-1][-1]["end"] < SECOND_GAP:
            groups[-1].append(seg)
        else:
            groups.append([seg])
    out, replaced = [], 0
    for g in groups:
        if not is_weak(g[0]):
            out += g
            continue
        cand, _ = filter_segments(decode_span(x, sr, g[0]["start"] - SECOND_PAD, g[-1]["end"] + SECOND_PAD,
                                              model, cfg, prompt), regs, prompt_words)
        if cand and weighted_logprob(cand) > weighted_logprob(g) + SECOND_MARGIN:
            was = " ".join(s["text"] for s in g)
            out += [{**c, "second_model": True, "replaced": was} for c in cand]
            replaced += 1
        else:
            out += g
    return out, replaced


def rescue(x, sr: int, model: str, cfg: Config, dropped: list[dict], regs: list[list[float]],
           prompt_words: set[str]) -> tuple[list[dict], list[dict]]:
    """Retranscribe spans where Whisper failed. No prompt: a loop is usually on a word from it."""
    kept, still = [], []
    for d in dropped:
        if not needs_rescue(d, regs):
            still.append(d)
            continue
        segs = decode_span(x, sr, d["start"] - RESCUE_PAD, d["end"] + RESCUE_PAD, model, cfg, None,
                           temperature=RESCUE_TEMPERATURES)
        good, bad = filter_segments(segs, regs, prompt_words)
        kept += [{**g, "rescued": True} for g in good]
        still.append({**d, "rescue": f"retry: {len(good)} segments, {sum(len(g['words']) for g in good)} words"})
        still += [{**x_, "rescued": True} for x_ in bad]
    return kept, still


def transcribe_track(wav: Path, cfg: Config, glossary: Glossary) -> dict:
    import mlx_whisper
    import soundfile as sf

    t0 = time.time()
    regs = vad.regions(wav)
    t_vad = time.time() - t0
    prompt, dropped_terms = glossary.build_prompt(token_counter(cfg.asr_language))
    prompt_words = set(_norm(prompt).split())
    result = {"model": cfg.asr_model, "language": cfg.asr_language, "prompt": prompt, "prompt_dropped_terms": dropped_terms,
              "audio_seconds": round(sf.info(str(wav)).duration, 3), "vad": [[round(a, 3), round(b, 3)] for a, b in regs],
              "vad_seconds": round(t_vad, 2), "segments": [], "dropped": []}
    if not regs:
        result["whisper_seconds"] = 0.0
        return result
    t1 = time.time()
    model = model_path(cfg.asr_model)
    second = model_path(cfg.second_model) if cfg.second_model else model
    x, sr = sf.read(str(wav), dtype="float32")   # otherwise mlx-whisper runs the file through ffmpeg again
    with _carry_prompt():
        r = mlx_whisper.transcribe(x, path_or_hf_repo=model, language=cfg.asr_language,
                                   word_timestamps=True, condition_on_previous_text=False,
                                   initial_prompt=prompt, temperature=MAIN_TEMPERATURES, verbose=None)
    result["whisper_seconds"] = round(time.time() - t1, 2)
    t2 = time.time()
    kept, dropped = filter_segments(r["segments"], regs, prompt_words)
    rescued, dropped = rescue(x, sr, second, cfg, dropped, regs, prompt_words)
    kept = sorted(kept + rescued, key=lambda s: s["start"])
    replaced = 0
    if cfg.second_model:
        kept, replaced = second_opinion(x, sr, kept, second, cfg, prompt, regs, prompt_words)
    result.update(segments=sorted(kept, key=lambda s: s["start"]), dropped=dropped, second_model=cfg.second_model,
                  rescued_segments=len(rescued), second_model_replaced=replaced,
                  second_pass_seconds=round(time.time() - t2, 2))
    return result


def run(folder: Path, meta: dict, cfg: Config, glossary: Glossary) -> dict:
    summary = {}
    prompt, dropped_terms = glossary.build_prompt(token_counter(cfg.asr_language))
    log.info(f"asr: prompt {prompt!r}" + (f"; did not fit: {', '.join(dropped_terms)}" if dropped_terms else ""))
    for track in ("mic", "remote"):
        res = transcribe_track(folder / f"{track}.16k.wav", cfg, glossary)
        (folder / f"asr.{track}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        words = sum(len(s["words"]) for s in res["segments"])
        reasons: dict[str, int] = {}
        for d in res["dropped"]:
            reasons[d["reason"]] = reasons.get(d["reason"], 0) + 1
        log.info(f"asr {track}: {res['audio_seconds']:.0f} s of audio, VAD speech "
                 f"{sum(b - a for a, b in res['vad']):.0f} s, whisper {res['whisper_seconds']:.1f} s, "
                 f"{len(res['segments'])} segments, {words} words, dropped {reasons or 0}")
        if res.get("rescued_segments") or res.get("second_model_replaced"):
            log.info(f"asr {track}: failures retranscribed +{res.get('rescued_segments', 0)} segments, "
                     f"second model replaced {res.get('second_model_replaced', 0)} weak spans "
                     f"({res.get('second_pass_seconds', 0):.1f} s)")
        summary[track] = {"segments": len(res["segments"]), "words": words, "dropped": reasons,
                          "whisper_seconds": res["whisper_seconds"]}
    return summary
