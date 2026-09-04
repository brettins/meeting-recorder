"""
Markdown → Notion block conversion.

Notes and transcripts are written as Markdown, and Notion's API takes a tree of
block objects instead. This module is the whole translation, kept pure and
gi-free so the awkward parts — inline spans, the 2000-character text ceiling,
the 100-child request ceiling — are unit-testable without a network or a token.

Only the Markdown the summarizer actually emits is supported: headings, bullet
lists, numbered lists, checkbox items, fenced code, block quotes, horizontal
rules, and paragraphs, with bold/italic/code spans inside them. Anything else
degrades to a paragraph rather than raising, because a formatting surprise must
never cost the user their notes.
"""

from __future__ import annotations

import re
from typing import Any

Block = dict[str, Any]

# Notion rejects a text object longer than 2000 characters, and an array of
# more than 100 of them in one block.
MAX_TEXT_LENGTH = 2000
MAX_RICH_TEXT_ITEMS = 100

# A single children request accepts at most 100 blocks.
MAX_BLOCKS_PER_REQUEST = 100

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_CHECKBOX = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s*(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_RULE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_FENCE = re.compile(r"^\s*```(\w*)\s*$")

_SPAN = re.compile(
    r"(\*\*\*.+?\*\*\*|\*\*.+?\*\*|__.+?__|(?<!\*)\*(?!\*).+?(?<!\*)\*(?!\*)|_.+?_|`.+?`)",
    re.DOTALL,
)

# Notion names its own subset of languages; anything unknown is rejected, so
# unrecognised fences fall back to plain text.
_CODE_LANGUAGES = {
    "bash",
    "c",
    "c++",
    "css",
    "diff",
    "docker",
    "go",
    "html",
    "java",
    "javascript",
    "json",
    "kotlin",
    "markdown",
    "python",
    "ruby",
    "rust",
    "shell",
    "sql",
    "swift",
    "typescript",
    "xml",
    "yaml",
}

_LANGUAGE_ALIASES = {
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "sh": "shell",
    "rb": "ruby",
    "kt": "kotlin",
    "yml": "yaml",
    "md": "markdown",
    "dockerfile": "docker",
    "cpp": "c++",
}


def _split_text(text: str) -> list[str]:
    """Break *text* into pieces Notion will accept, preferring word boundaries."""
    if len(text) <= MAX_TEXT_LENGTH:
        return [text]
    pieces: list[str] = []
    rest = text
    while len(rest) > MAX_TEXT_LENGTH:
        window = rest[:MAX_TEXT_LENGTH]
        cut = window.rfind(" ")
        if cut < MAX_TEXT_LENGTH // 2:
            cut = MAX_TEXT_LENGTH
        pieces.append(rest[:cut])
        rest = rest[cut:].lstrip()
    if rest:
        pieces.append(rest)
    return pieces


def _text_object(content: str, annotations: dict[str, bool] | None = None) -> Block:
    obj: Block = {"type": "text", "text": {"content": content}}
    if annotations:
        obj["annotations"] = dict(annotations)
    return obj


def rich_text(markdown: str) -> list[Block]:
    """Convert one line of inline Markdown into Notion rich-text objects.

    Bold, italic, bold-italic and inline code are recognised; the markers are
    dropped and carried as annotations instead. Unmatched markers stay as
    literal characters, which is what a reader of the Markdown would see.
    """
    if not markdown:
        return []
    items: list[Block] = []

    def emit(content: str, annotations: dict[str, bool] | None = None) -> None:
        for piece in _split_text(content):
            if piece:
                items.append(_text_object(piece, annotations))

    for part in _SPAN.split(markdown):
        if not part:
            continue
        if part.startswith("***") and part.endswith("***") and len(part) > 6:
            emit(part[3:-3], {"bold": True, "italic": True})
        elif part.startswith("**") and part.endswith("**") and len(part) > 4:
            emit(part[2:-2], {"bold": True})
        elif part.startswith("__") and part.endswith("__") and len(part) > 4:
            emit(part[2:-2], {"bold": True})
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            emit(part[1:-1], {"code": True})
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            emit(part[1:-1], {"italic": True})
        elif part.startswith("_") and part.endswith("_") and len(part) > 2:
            emit(part[1:-1], {"italic": True})
        else:
            emit(part)
    return items[:MAX_RICH_TEXT_ITEMS]


def _block(kind: str, markdown: str, **extra: object) -> Block:
    payload: Block = {"rich_text": rich_text(markdown)}
    payload.update(extra)
    return {"object": "block", "type": kind, kind: payload}


def paragraph(text: str) -> Block:
    """A plain paragraph block carrying *text* verbatim, markers and all."""
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [_text_object(p) for p in _split_text(text)]},
    }


def _code_language(fence: str) -> str:
    name = fence.strip().lower()
    name = _LANGUAGE_ALIASES.get(name, name)
    return name if name in _CODE_LANGUAGES else "plain text"


def markdown_to_blocks(markdown: str) -> list[Block]:
    """Convert a Markdown document into a flat list of Notion blocks.

    Lists stay flat: Notion nests through a child array, and the summarizer
    does not emit nested lists, so indentation is treated as ordinary text
    rather than guessed at.
    """
    blocks: list[Block] = []
    lines = markdown.replace("\r\n", "\n").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        fence = _FENCE.match(line)
        if fence:
            language = _code_language(fence.group(1))
            index += 1
            body: list[str] = []
            while index < len(lines) and not _FENCE.match(lines[index]):
                body.append(lines[index])
                index += 1
            index += 1  # step over the closing fence, if there was one
            code = "\n".join(body)
            blocks.append(
                {
                    "object": "block",
                    "type": "code",
                    "code": {
                        "rich_text": [_text_object(p) for p in _split_text(code)],
                        "language": language,
                    },
                }
            )
            continue

        index += 1

        if not stripped:
            continue

        if _RULE.match(line):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            continue

        checkbox = _CHECKBOX.match(line)
        if checkbox:
            done = checkbox.group(1).lower() == "x"
            blocks.append(_block("to_do", checkbox.group(2), checked=done))
            continue

        heading = _HEADING.match(stripped)
        if heading:
            level = min(len(heading.group(1)), 3)
            blocks.append(_block(f"heading_{level}", heading.group(2)))
            continue

        quote = _QUOTE.match(stripped)
        if quote:
            blocks.append(_block("quote", quote.group(1)))
            continue

        bullet = _BULLET.match(line)
        if bullet:
            blocks.append(_block("bulleted_list_item", bullet.group(1)))
            continue

        numbered = _NUMBERED.match(line)
        if numbered:
            blocks.append(_block("numbered_list_item", numbered.group(1)))
            continue

        blocks.append(_block("paragraph", stripped))

    return blocks


def transcript_toggle(transcript: str, label: str = "Transcript") -> Block:
    """Wrap *transcript* in a collapsed toggle so it does not swamp the page.

    A toggle carries at most 100 children in the request that creates it, and
    a long transcript overruns that, so the paragraphs are packed to fill each
    child block rather than mapped one line to one block.
    """
    paragraphs = [p.strip() for p in transcript.replace("\r\n", "\n").split("\n\n")]
    packed = _pack_paragraphs([p for p in paragraphs if p])
    return {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": rich_text(label),
            "children": [paragraph(p) for p in packed[:MAX_BLOCKS_PER_REQUEST]],
        },
    }


def _pack_paragraphs(paragraphs: list[str]) -> list[str]:
    """Join consecutive paragraphs up to the per-text ceiling.

    A transcript arrives as many short turns; one block each would blow the
    100-child limit within a couple of minutes of speech.
    """
    packed: list[str] = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > MAX_TEXT_LENGTH and current:
            packed.append(current)
            current = para
        else:
            current = candidate
    if current:
        packed.append(current)
    return packed


def chunk_blocks(blocks: list[Block]) -> list[list[Block]]:
    """Split *blocks* into batches Notion will accept in one append call."""
    return [
        blocks[i : i + MAX_BLOCKS_PER_REQUEST]
        for i in range(0, len(blocks), MAX_BLOCKS_PER_REQUEST)
    ]
