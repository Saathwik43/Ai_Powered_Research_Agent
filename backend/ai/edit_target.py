"""
Element-level targeting for manuscript revisions (audit Tier 5, E7).

The problem this solves: a revision used to hand the model the whole section and
*ask* it to change only what the instructions named. The model does not edit
text -- it re-emits it. At temperature 0.45 that means untouched paragraphs come
back subtly reworded, so "fix the chart" quietly rewrites prose nobody asked
about. The prompt rule added in E5 makes that less likely; it cannot make it
impossible.

Targeting makes it impossible. The model still *reads* the whole section (edit
quality depends on that context), but it only ever *emits* a replacement for one
span. Everything outside the span is copied byte-for-byte from the original by
`splice()` below. Untargeted text is not preserved by good behaviour; it is
preserved because it never passes through the model's output.
"""

import re

_TARGET_TAG_RE = re.compile(r"</?target>", re.IGNORECASE)
_WHOLE_FENCE_RE = re.compile(r"\A```[^\n`]*\n(.*?)\n?```\Z", re.DOTALL)

# An "overrun" is the model ignoring rule 2 and echoing surrounding text back.
# Splicing that duplicates content, so it is flagged rather than swallowed --
# the diff shows the damage and the user rejects.
_OVERRUN_FLOOR = 400
_OVERRUN_RATIO = 4


def trim_span(content: str, start: int, end: int):
    """
    Shrink a span past its own leading/trailing whitespace.

    The whitespace then belongs to the *preserved* text on either side, so a
    replacement can never eat the blank line before a paragraph or weld two
    paragraphs together.
    """
    while start < end and content[start].isspace():
        start += 1
    while end > start and content[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def resolve_span(content: str, target_text: str, start=None, end=None):
    """
    Locate *target_text* inside *content*, returning a trimmed ``(start, end)``.

    Offsets are trusted only when they still hold the text the client claims --
    the section can be edited by hand between selecting and revising. When they
    do not, fall back to a literal search, and refuse an ambiguous match rather
    than silently revise the wrong copy.
    """
    if not content or not target_text or not target_text.strip():
        return None

    if isinstance(start, int) and isinstance(end, int) and not isinstance(start, bool):
        if 0 <= start < end <= len(content) and content[start:end] == target_text:
            return trim_span(content, start, end)

    first = content.find(target_text)
    if first == -1 or content.find(target_text, first + 1) != -1:
        return None
    return trim_span(content, first, first + len(target_text))


def unwrap_echo(result: str, unwrap_fence: bool = False) -> str:
    """
    Strip wrappers the model adds that the splice would otherwise nest.

    ``unwrap_fence`` is set for a diagram target, where the span is the *body*
    of a ```mermaid block and the fences live outside it -- a fence in the reply
    would end up nested inside the real one.
    """
    text = _TARGET_TAG_RE.sub("", result or "").strip()
    if unwrap_fence:
        match = _WHOLE_FENCE_RE.match(text)
        if match:
            text = match.group(1).strip()
    return text


def splice(content: str, start: int, end: int, replacement: str) -> str:
    return content[:start] + replacement + content[end:]


def looks_overrun(target_text: str, replacement: str) -> bool:
    """The reply is far larger than what it replaces -- rule 2 was ignored."""
    if not replacement:
        return False
    return len(replacement) > max(_OVERRUN_FLOOR, _OVERRUN_RATIO * len(target_text))


def mark_target(content: str, start: int, end: int) -> str:
    """Render the section with the editable span called out for the model."""
    return f"{content[:start]}⟦EDIT⟧{content[start:end]}⟦/EDIT⟧{content[end:]}"
