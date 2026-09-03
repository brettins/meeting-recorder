# Wishlist

What this fork wants, and what it already has. The July 2026 architecture audit has its
own record in [`audit-roadmap-2026-07.md`](audit-roadmap-2026-07.md); this file picks up
where that left off and tracks fork-local work.

The fork has GitHub issues disabled and treats the remote as a backup, so ideas were
living in chat history and nowhere else. That is what this file replaces. Add a line when
an idea comes up, move it down when it ships, and say why if it gets dropped.

Legend: **L** = Linux app, **A** = Android app, **I** = infrastructure and packaging.

---

## Open

Nothing here is scheduled. Order is rough priority.

### L — Reconsider the action crowding on a failed row

A failed meeting in the library shows Details, Retry, Dismiss and then the four meeting
buttons — seven controls on one line. Each one earns its place, and the row still reads
as cluttered at narrow widths. An overflow menu for the meeting half is one option.

### L — Window title and tray tooltip do not name the meeting

Both say "Meeting Recorder" whatever is happening. With the title editable during a
recording there is now something specific to show.

### I — Upstream sync

The fork is 13 commits ahead of `dipakmdhrm/meeting-recorder` and carries a fork-local
override in CLAUDE.md that upstream must not receive. Decide which of tags, the unified
row, the daemon-side title editing and the packaging fixes are worth sending back, and
strip the override from any branch that goes.

### I — Issue #65 looks stale, confirm and close

Upstream #65 says the key validation rejects the newer `AQ...` prefix format.
`config/settings.py:gemini_key_warning` checks for embedded whitespace and a minimum
length and has no prefix rule at all, so the report seems to predate that rewrite.
Confirm against the version the reporter ran before closing.

---

## Done — fork

| Ships as | What |
| --- | --- |
| #1 | Tags, plus a Gemini model list fetched from the API rather than hardcoded |
| #2 | Tagging from the Record tab, before a recording starts |
| #3 | Tag colours retuned per theme to meet WCAG 1.4.3 and 1.4.11 |
| #4 | Errors surfaced in the UI; capped Gemini thinking budget for transcription |
| #5 | `.deb` version derived from a git tag |
| #6 | Local-merge workflow: GitHub is a backup, not the gate |
| local | Post-merge hook rebuilds the `.deb` when `main` moves |
| local | One row type for meetings and jobs; Stop button fixed; live retitling |
| local | Keyring guard: a redirected `HOME` can no longer overwrite the live API key |
| local | `dpkg -i` stops the old daemon; `__pycache__` kept out of the package |

## Done — upstream audit

All 23 items, PRs #25 to #45. See [`audit-roadmap-2026-07.md`](audit-roadmap-2026-07.md).

---

## Decided against

### Rewriting the Linux app in another language

Evaluated during the July audit and rejected. The problems sat in two UI god-objects and
ad-hoc threading, roughly 40% of a small codebase. The parts that would have been hardest
to rebuild — the segment-based ffmpeg recorder, the pure-DBus tray, multi-distro
packaging, pip-based local-engine installs — were the parts already working.

### Swapping Android off `MANAGE_EXTERNAL_STORAGE`

The app ships as a GitHub APK, and the shared `Documents/Meetings/` tree survives
uninstall and is the same on-disk format the Linux app reads. If Play Store distribution
ever becomes a goal, the path is a SAF tree grant over that same directory. Until then
the permission model stays.

### Running AI processing inside the daemon

Importing `google.genai` alone costs about 70 MB of RSS and Python never unloads a
module, so the daemon would keep that memory for its whole life. Each job runs in a
short-lived `--process` child instead, and daemon idle stays near 40 MB.
