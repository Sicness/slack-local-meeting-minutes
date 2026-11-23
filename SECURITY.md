# Security policy

This app handles recordings of conversations and voiceprints, so privacy bugs count as security bugs.

## Reporting a vulnerability

Report it privately through [GitHub's vulnerability reporting](../../security/advisories/new), not in a public issue. Never include real meeting data in a report.

## In scope

- Audio, text or voice data leaving the Mac
- Access to recordings, transcripts or voiceprints by another user, process or website
- Bypassing the web UI's session token or its Host and Origin checks
- Model or speech text acting as active content in notes or the web UI
- Data that remains after `ma delete` or `ma voices delete`, including in logs

## Known limits

- Anyone with access to your macOS account can read your data. Use FileVault
- Notes in your Obsidian vault are outside the app's private data folder, and any sync you enable for the vault applies to them
- Deleting a voice leaves that person's words in transcripts, attributed to "Unknown"
