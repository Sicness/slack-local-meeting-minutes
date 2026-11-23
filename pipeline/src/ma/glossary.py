"""Glossary at ~/MeetingAssistant/glossary.txt.

Format: one term per line; a leading `!` puts it into the Whisper prompt; `wrong => right` is a replacement
after recognition; `#` is a comment. Line order = prompt priority (Whisper's limit is ~224 tokens).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import config, log, paths

PROMPT_HEADS = {"ru": "Созвон команды.", "en": "Team call."}
PROMPT_TOKENS = 220   # the Whisper decoder allows 223 prompt tokens

# Common Whisper errors in Russian speech with English terms.
STARTER_REPLACEMENTS = [
    "деплай => деплой",
    "задеплаить => задеплоить",
    "графене => Grafana",
    "графана => Grafana",
    "графану => Grafana",
    "экшнайтим => экшн-айтем",
    "экшн айтем => экшн-айтем",
    "опрувим => апрувим",
    "членжил => челенжил",
    "бэклэк => бэклог",
    "скауп => скоуп",
    "скаупа => скоупа",
    "скаупу => скоупу",
    "скаупом => скоупом",
    "скаупе => скоупе",
    "ассоп => асап",
    "асаап => асап",
    "авс => AWS",
    "jira => Jira",
    "джира => Jira",
    "джиру => Jira",
]


DATA = Path(__file__).parent / "data"
REPLACEMENT_SECTIONS = ("замены", "replacements")
REPLACEMENTS_TITLE = {"ru": "# Замены после распознавания", "en": "# Replacements after recognition"}
FILE_HEADER = {
    "ru": ["# Словарь Meeting Assistant. Строка — термин; «!» в начале — в подсказку Whisper",
           "# (порядок = приоритет, лимит ~220 токенов); «ошибка => правильно» — замена после распознавания.",
           "# Сюда же — названия проектов и имена коллег."],
    "en": ["# Meeting Assistant glossary. One term per line; a leading \"!\" puts it into the Whisper prompt",
           "# (order = priority, ~220 token limit); \"wrong => right\" is a replacement after recognition.",
           "# Add your project names and colleagues' names here."],
}


@dataclass
class Glossary:
    prompt_terms: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    replacements: list[tuple[str, str]] = field(default_factory=list)
    head: str = PROMPT_HEADS["en"]

    def build_prompt(self, count_tokens: Callable[[str], int], limit: int = PROMPT_TOKENS) -> tuple[str, list[str]]:
        """The Whisper prompt and the terms that did not fit."""
        def text(items: list[str]) -> str:
            return f"{self.head} {', '.join(items)}.".strip() if items else self.head

        items, dropped = [], []
        for term in self.prompt_terms:
            if count_tokens(text(items + [term])) <= limit:
                items.append(term)
            else:
                dropped.append(term)
        return text(items), dropped

    def replace(self, text: str) -> str:
        for wrong, right in self.replacements:
            text = re.sub(rf"(?<!\w){re.escape(wrong)}(?!\w)", right, text, flags=re.IGNORECASE)
        return text


def parse(text: str) -> Glossary:
    g = Glossary()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=>" in line:
            wrong, right = (s.strip() for s in line.split("=>", 1))
            if wrong and right:
                g.replacements.append((wrong, right))
            continue
        important = line.startswith("!")
        term = line.lstrip("!").strip()
        if not term:
            continue
        g.terms.append(term)
        if important:
            g.prompt_terms.append(term)
    return g


def from_markdown(md: str, language: str = "ru") -> str:
    """Starter glossary.txt from a markdown glossary: list lines `- ! term — explanation`."""
    out = list(FILE_HEADER[language]) + [""]
    section = None
    for raw in md.splitlines():
        if raw.startswith("## "):
            section = raw[3:].strip()
            if not section.lower().startswith(REPLACEMENT_SECTIONS):
                out += ["", f"# {section}"]
            continue
        m = re.match(r"^- (!\s*)?(.+?)(?:\s+—\s+.*)?$", raw)
        if not m or section is None or section.lower().startswith(REPLACEMENT_SECTIONS):
            continue
        out.append(("! " if m.group(1) else "") + m.group(2).strip())
    out += ["", REPLACEMENTS_TITLE[language]] + (STARTER_REPLACEMENTS if language == "ru" else [])
    return "\n".join(out) + "\n"


def load(path: Path | None = None, speech_language: str | None = None) -> Glossary:
    path = path or paths.glossary()
    speech_language = speech_language or config.load().asr_language
    starter = "ru" if speech_language == "ru" else "en"
    if not path.exists():
        source = DATA / f"glossary.{starter}.md"
        text = from_markdown(source.read_text(encoding="utf-8"), starter)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        log.info(f"glossary: created {path} from {source.name}; add your own terms and names to it")
    g = parse(path.read_text(encoding="utf-8"))
    g.head = PROMPT_HEADS.get(speech_language, "")
    return g
