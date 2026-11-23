# Minutes format

The model (local, through Ollama, D10) returns **JSON that follows a schema**. Our code in `pipeline/src/ma/notes.py` builds the Markdown. The format can change without touching the prompt, and names are filled in at render time.

Notes come in English or Russian, set by the root `language` in `config.toml`. The structure is the same in both. Headings and fixed strings come from the catalog in [pipeline/src/ma/i18n.py](../pipeline/src/ma/i18n.py). The examples below show English.

## Minutes note

```markdown
---
date: "2026-01-15 15:30"
duration: "47 min"
participants: ["[[Alex]]", "[[Sam]]", "[[Priya]]", "Voice 4"]
recording_id: "2026-01-15_1530_ab12"
source: "slack"
tags: ["meeting"]
transcript: "[[Transcripts/2026-01-15 1530 — Checkout rollout]]"
---

<!-- ma:begin -->
# Checkout rollout

**Summary.** The team agreed to release the new checkout to 10% of users on Thursday. Sam will finish the limit check by Wednesday. Weekend support is still open.

## Decisions
- Release to 10% on Thursday, full launch after a week of metrics ([[Sam]], [[Alex]]) ([[Transcripts/2026-01-15 1530 — Checkout rollout#^t001230|00:12:31]])
- First-purchase limit is 500 USD ([[Transcripts/…#^t002004|00:20:10]])

## Action items
- [ ] [[Sam]] — finish the limit check — by Wednesday ([[Transcripts/…#^t003140|00:31:45]])
- [ ] [[Alex]] — set up an alert on the failure rate — by Thursday (…)
- [ ] Voice 4 — prepare a support FAQ — no due date (…)
- [ ] unassigned — update the limits doc — no due date (…)

## Open questions and concerns
- Who answers checkout tickets on weekends
- [[Priya]] questions the 10% figure and suggests 5%

## Discussion
1. **Limits.** Whether new users need a separate limit. …
2. **Support.** …

[[Transcripts/2026-01-15 1530 — Checkout rollout|Full transcript]] · _minutes: qwen3.5:9b, verify against the timestamp links_
<!-- ma:end -->
```

Rendering rules:

- Action items are Obsidian checkboxes, so you can tick them and collect them with the Tasks plugin
- A due date appears only if one was said. Otherwise the item says "no due date"
- An unknown voice stays "Voice N" without a link. Once it gets a name, a re-render turns it into `[[Name]]`
- Terms and jargon stay as spoken
- Each decision and action item links to the transcript paragraph of its source utterance. A local model can miss or distort things, and the link makes checking quick
- An action item nobody took is "unassigned"
- An empty Decisions, Action items or Open questions section shows a single `- —`
- Model and speech text goes through `notes.safe`, so it cannot act as HTML, a block marker or an embed

## Response schema

Passed to Ollama as `format` (`SCHEMA` in [pipeline/src/ma/minutes.py](../pipeline/src/ma/minutes.py)):

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["title", "summary", "decisions", "action_items", "open_questions", "topics"],
  "properties": {
    "title":   {"type": "string"},
    "summary": {"type": "string"},
    "decisions": {"type": "array", "items": {
      "type": "object", "additionalProperties": false, "required": ["at", "text", "who"],
      "properties": {
        "at":   {"type": "string"},
        "text": {"type": "string"},
        "who":  {"type": "array", "items": {"type": "string"}}
      }}},
    "action_items": {"type": "array", "items": {
      "type": "object", "additionalProperties": false, "required": ["at", "owner", "task", "due"],
      "properties": {
        "at":    {"type": "string"},
        "owner": {"type": "string"},
        "task":  {"type": "string"},
        "due":   {"type": "string"}
      }}},
    "open_questions": {"type": "array", "items": {"type": "string"}},
    "topics": {"type": "array", "items": {
      "type": "object", "additionalProperties": false, "required": ["title", "points"],
      "properties": {
        "title":  {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}}
      }}}
  }
}
```

| Field | Meaning |
|---|---|
| `title` | meeting topic, 3–7 words |
| `summary` | 2–4 sentences: the main point and how it ended |
| `decisions[].at`, `action_items[].at` | time of the source utterance, `hh:mm:ss` |
| `decisions[].who` | labels of those who made or proposed the decision |
| `action_items[].owner` | label of the person, or empty |
| `action_items[].due` | due date as spoken, or empty |
| `open_questions` | unresolved matters and concerns |
| `topics` | what was discussed, with short points, including each participant's update |

The model refers to people **only by labels**, also inside text fields (`@v3 proposed…`). Labels are `@me`, `@v1`, `@v2`…, `@room` and `@unknown`. The code replaces them with `[[Name]]`, keeps "Voice N" as plain text, and drops labels that are not in the meeting from `owner` and `who`. `at` is normalized to `hh:mm:ss`.

The result is stored in `minutes.json` with the model, prompt version, language, reasoning flag, timing, token counts and warnings.

## Prompt

The prompts are `SYSTEM_EN` and `SYSTEM_RU` in [pipeline/src/ma/minutes.py](../pipeline/src/ma/minutes.py) (`PROMPT_VERSION = 3`). Root `language` selects the prompt and the language of the minutes.

The model receives the participants with known names (so it can resolve people addressed by name), the glossary, and the transcript, one line per utterance: `[hh:mm:ss] @label: text`.

Rules in both prompts:

- Refer to people only by labels, everywhere, including `title` and `summary`
- First go through the transcript participant by participant (what they reported, proposed, took on, were asked to do), then assemble the minutes. Otherwise individual updates get lost
- Invent nothing. Each decision and action item has `at`, the time it was first said
- A decision is only an explicit agreement. An unconfirmed proposal is an open question. A status report, one person's plan, a complaint or an instruction during the call is a topic point
- An action item is something someone took on or was directly asked to do. A request to everyone has an empty `owner`. Never guess the owner
- `due` only if a date, weekday or time was said. "Before the next meeting" and "after X" are not due dates
- What was found out or calculated during the meeting is a topic point, not an action item
- Do not interpret or quote unintelligible passages
- Keep terms and jargon as spoken

The English prompt adds two instructions: keep reasoning brief without re-checking the result, and count one agreement as one decision.

Request settings: `temperature` 0.2, `num_predict` = `[minutes] max_tokens`, `think` = `[minutes] think`, `keep_alive` 0. If reasoning hits the cap or the reply is not JSON, the request is retried without reasoning.

**Known limits.** Treat the minutes as a draft. On long meetings the model can miss key points, most often requests to the team. It sometimes promotes an opinion to a decision, and `at` can point to a neighboring utterance. The timestamp links are there to catch this.

## Transcript note

```markdown
---
date: "2026-01-15 15:30"
duration: "47 min"
participants: ["[[Alex]]", "[[Sam]]", "[[Priya]]", "Voice 4"]
recording_id: "2026-01-15_1530_ab12"
source: "slack"
tags: ["meeting-transcript"]
meeting: "[[Meetings/2026-01-15 1530 — Checkout rollout]]"
---

<!-- ma:begin -->
Minutes: [[Meetings/2026-01-15 1530 — Checkout rollout]]

**00:00:04 [[Alex]]:** Hi, let's start with the limits. ^t000004

**00:00:15 [[Sam]]:** I have a concern about the first purchase. ^t000015
<!-- ma:end -->
```

Consecutive utterances of one person are joined into one paragraph. The timestamp and the block id mark the start of the paragraph. Low-confidence utterances are marked `(?)`.
