"""minutes step: meeting minutes from a local model via Ollama → minutes.json.

The model gets the labeled transcript (@me, @v3), the participant list with names and the glossary, and replies with
schema-bound JSON. People appear only as labels: the notes step fills in names, so `ma voices name` does not recompute
the minutes. Every decision and action item carries the time of its source utterance (`at`): this makes invention
harder for the model and checking quick for the user.
"""

import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from . import i18n, log
from .config import Config
from .glossary import Glossary
from .transcript import clock

PROMPT_VERSION = {"en": 1, "ru": 4}
LABEL = re.compile(r"@(?:me|room|unknown|v\d+)\b")
AT = re.compile(r"\d{1,2}:\d{2}:\d{2}")

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "summary", "decisions", "action_items", "open_questions", "topics"],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "decisions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["at", "text", "who"],
            "properties": {"at": {"type": "string"}, "text": {"type": "string"},
                           "who": {"type": "array", "items": {"type": "string"}}}}},
        "action_items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["at", "owner", "task", "due"],
            "properties": {"at": {"type": "string"}, "owner": {"type": "string"}, "task": {"type": "string"},
                           "due": {"type": "string"}}}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "topics": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "points"],
            "properties": {"title": {"type": "string"}, "points": {"type": "array", "items": {"type": "string"}}}}},
    },
}

SYSTEM_RU = """Ты пишешь протокол рабочей встречи. На вход — расшифровка созвона с метками говорящих: @me — {me}, владелец записи; @v1, @v2… — другие участники. Кого как зовут — в списке участников; если имени нет, человек известен только по метке. Расшифровка сделана автоматически: в ней бывают ошибки распознавания, особенно в английских терминах. Словарь ниже показывает, как термины пишутся правильно. Неразборчивые места не толкуй и не переписывай дословно — пропусти.

Пиши по-русски, коротко и по делу, как пишет человек для коллег, а не канцелярским языком. Термины и сленг оставляй как говорили. Людей называй только метками (@me, @v3) — везде, включая title и summary, даже если знаешь имя: имена подставит программа. Когда в разговоре обращаются по имени («Петя, возьмёшь?»), по списку участников пойми, чья это метка.

Как работать: сначала пройди расшифровку по каждому участнику — что он рассказал, что предложил, за что взялся, о чём попросили его, — чтобы ничей доклад не потерялся. Потом собери протокол.

Правила, нарушать нельзя:
- Ничего не придумывай. Каждое решение и каждая задача опираются на конкретную реплику: в поле at — время реплики, где это прозвучало впервые, [чч:мм:сс].
- Решение — только то, о чём явно договорились («делаем так», «решили», «ок, тогда…»). Предложение, которое никто не подтвердил, — открытый вопрос. Рассказ о статусе, план одного человека, жалоба, инструкция по ходу звонка, проверка связи — не решение и не задача; это пункты тем.
- Задача — то, что кто-то взялся сделать или о чём его прямо попросили после встречи. owner — метка того, кто взялся или кого попросили. Просьба ко всем или «надо бы» без исполнителя — задача с пустым owner, а не задача того, кто просит. Не назначай исполнителя по догадке.
- due — только если прозвучала дата, день недели или время («до пятницы», «завтра», «к 15:00»). Иначе пусто. «До следующей встречи», «после X», «скоро» — не срок, если так не сказали прямо.
- Что выяснили, посчитали или сделали прямо на встрече, с цифрами, — пункт темы, а не задача.
- Нерешённое и консерны — open_questions.
- Пустые разделы оставляй пустыми массивами.

Поля ответа: title — тема встречи, 3–7 слов; summary — 2–4 предложения: главное и чем закончилось; decisions — решения (who — метки тех, кто принял или предложил); action_items — задачи; open_questions — что осталось нерешённым и консерны; topics — о чём говорили, по темам, с короткими пунктами, включая доклады каждого участника.

Словарь: {glossary}"""

SYSTEM_EN = """You are writing the minutes of a work meeting. The input is an automatic transcript of a call with speaker labels: @me is {me}, the owner of the recording; @v1, @v2… are other participants. Their names, when known, are in the participant list; otherwise a person is known only by label. The transcript contains recognition errors, especially in technical terms. The glossary below shows how terms are spelled. Do not interpret or quote unintelligible passages: skip them.

Write in English, briefly and to the point, the way a person writes for colleagues, not in corporate language. Keep terms and jargon as spoken. Refer to people only by labels (@me, @v3) everywhere, including title and summary, even if you know the name: the program fills names in. When someone is addressed by name ("Pete, can you take it?"), use the participant list to work out whose label it is.

How to work: first go through the transcript participant by participant (what they reported, what they proposed, what they took on, what they were asked to do) so that nobody's update is lost. Then assemble the minutes. Keep your reasoning brief and do not re-check the finished result.

Rules you must not break:
- Invent nothing. Every decision and action item rests on a specific utterance: put the time where it was first said into the at field, [hh:mm:ss].
- A decision is only what was explicitly agreed ("let's do that", "decided", "ok, then…"). One agreement is one decision: do not list its confirmation separately. A proposal nobody confirmed is an open question. A status report, one person's plan, a complaint, an instruction during the call or a connection check is neither a decision nor an action item; it is a topic point.
- An action item is something someone took on or was directly asked to do after the meeting. owner is the label of that person. A request to everyone, or "someone should" / "we should" with no assignee, is still an action item, with an empty owner; it is not a task for whoever asked. Never guess the owner.
- due only if a date, weekday or time was said ("by Friday", "tomorrow", "by 3 pm"). Otherwise leave it empty. "Before the next meeting", "after X" and "soon" are not due dates unless stated outright.
- What was found out, calculated or done during the meeting, with numbers, is a topic point, not an action item.
- Unresolved matters and concerns go to open_questions.
- Leave empty sections as empty arrays.

Response fields: title is the meeting topic, 3–7 words; summary is 2–4 sentences: the main point and how it ended; decisions are the decisions (who holds the labels of those who made or proposed them); action_items are the action items; open_questions are unresolved matters and concerns; topics are what was discussed, by topic, with short points, including each participant's update.

Glossary: {glossary}"""

SYSTEM = {"en": SYSTEM_EN, "ru": SYSTEM_RU}

TEXT = {
    "en": {"participants": "Participants", "transcript": "Transcript", "no_name": "name unknown",
           "room": "{me}'s microphone while audio played through speakers: {me} or a remote speaker",
           "unknown": "a participant who could not be identified"},
    "ru": {"participants": "Участники", "transcript": "Расшифровка", "no_name": "имя неизвестно",
           "room": "микрофон {me}, когда звук шёл в динамики: {me} или собеседник из динамиков",
           "unknown": "собеседник, которого не удалось опознать"},
}


class MinutesError(RuntimeError):
    pass


def participants(transcript: dict, me: str, language: str = "en") -> str:
    text = TEXT[language]
    rows = [f"@me — {me}"]
    for lab, name in transcript.get("speakers", {}).items():
        if lab.startswith("@v"):
            rows.append(f"{lab} — {text['no_name']}" if i18n.is_unnamed(name) else f"{lab} — {name}")
    if "@room" in transcript.get("speakers", {}):
        rows.append("@room — " + text["room"].replace("{me}", me))
    if "@unknown" in transcript.get("speakers", {}):
        rows.append("@unknown — " + text["unknown"])
    return "\n".join(rows)


def user_message(transcript: dict, me: str, language: str = "en") -> str:
    lines = "\n".join(f"[{clock(u['start'])}] {u['speaker']}: {u['text']}" for u in transcript["utterances"])
    text = TEXT[language]
    return f"{text['participants']}:\n{participants(transcript, me, language)}\n\n{text['transcript']}:\n{lines}"


def context_size(system: str, user: str, max_tokens: int) -> int:
    """Context window: Russian input is ~2.5 chars per token with a margin, plus the reply with reasoning."""
    need = (len(system) + len(user)) / 2.5 + max_tokens + 1024
    size = 16384
    while size < need and size < 262144:
        size *= 2
    return size


def _post(cfg: Config, body: dict, timeout: float = 1800) -> dict:
    req = urllib.request.Request(f"{cfg.ollama_url}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise MinutesError(f"Ollama returned {e.code}: {e.read().decode(errors='replace')[:300]}") from None
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise MinutesError(f"Ollama is unavailable at {cfg.ollama_url} ({e}): start Ollama.app and rerun "
                           "`ma process <id>`; it resumes from the minutes step") from None


def ask(cfg: Config, system: str, user: str, think: bool) -> tuple[dict | None, dict]:
    body = {"model": cfg.minutes_model, "stream": False, "format": SCHEMA, "think": think, "keep_alive": 0,
            "options": {"num_ctx": context_size(system, user, cfg.minutes_max_tokens), "temperature": 0.2,
                        "num_predict": cfg.minutes_max_tokens},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    d = _post(cfg, body)
    if d.get("done_reason") == "length":
        return None, d
    try:
        return json.loads(d["message"]["content"]), d
    except (KeyError, json.JSONDecodeError):
        return None, d


def clean(m: dict, labels: set[str]) -> tuple[dict, list[str]]:
    """Labels not in the meeting are removed; `at` is normalized to hh:mm:ss."""
    notes = []

    def at(v: str) -> str:
        f = AT.search(v or "")
        if not f:
            return ""
        h, mnt, s = f.group(0).split(":")
        return f"{int(h):02d}:{mnt}:{s}"

    for a in m["action_items"]:
        a["at"] = at(a.get("at", ""))
        if a["owner"] and a["owner"] not in labels:
            notes.append(f"owner {a['owner']!r} is not a meeting participant, removed")
            a["owner"] = ""
    for d in m["decisions"]:
        d["at"] = at(d.get("at", ""))
        bad = [w for w in d["who"] if w not in labels]
        if bad:
            notes.append(f"decision has non-participant labels {bad}, removed")
            d["who"] = [w for w in d["who"] if w in labels]
    return m, notes


def run(folder: Path, meta: dict, cfg: Config, glossary: Glossary) -> dict:
    out = folder / "minutes.json"
    duration = float(meta.get("duration_seconds") or 0)
    if duration < cfg.min_meeting_seconds:
        out.unlink(missing_ok=True)
        log.info(f"minutes: meeting shorter than {cfg.min_meeting_seconds:.0f} s, no minutes needed")
        return {"skipped": "short"}
    transcript = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    if not transcript["utterances"]:
        out.unlink(missing_ok=True)
        log.info("minutes: transcript is empty, no minutes needed")
        return {"skipped": "empty"}
    system = SYSTEM[cfg.language].replace("{me}", cfg.me_name).replace("{glossary}", ", ".join(glossary.terms))
    user = user_message(transcript, cfg.me_name, cfg.language)

    started = datetime.now()
    m, d = ask(cfg, system, user, cfg.minutes_think)
    think = cfg.minutes_think
    if m is None and think:
        # reasoning hit the cap or the reply is not JSON: retry without reasoning (it can loop)
        log.warn(f"minutes: no reply with reasoning ({d.get('done_reason')}), retrying without it")
        m, d = ask(cfg, system, user, False)
        think = False
    if m is None:
        raise MinutesError(f"model returned no minutes ({d.get('done_reason')})")
    labels = set(transcript.get("speakers", {}))
    m, notes = clean(m, labels)
    for n in notes:
        log.warn(f"minutes: {n}")
    seconds = (datetime.now() - started).total_seconds()
    res = {"version": 1, "prompt_version": PROMPT_VERSION[cfg.language], "language": cfg.language, "model": cfg.minutes_model, "think": think,
           "created_at": started.astimezone().isoformat(timespec="seconds"), "seconds": round(seconds, 1),
           "tokens": {"prompt": d.get("prompt_eval_count"), "output": d.get("eval_count")},
           "warnings": notes, "minutes": m}
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log.info(f"minutes: {cfg.minutes_model}{' (reasoning)' if think else ''} in {seconds:.0f} s, "
             f"{len(m['decisions'])} decisions, {len(m['action_items'])} action items, {len(m['open_questions'])} questions")
    return {"model": cfg.minutes_model, "think": think, "seconds": round(seconds, 1),
            "decisions": len(m["decisions"]), "action_items": len(m["action_items"])}
