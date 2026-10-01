"""Untrusted text (proposals, papers, chunks) is always wrapped in <document> tags as data."""

from __future__ import annotations

import re

_TAG = re.compile(r"<(/?)(document)", re.I)
LOOKALIKE_LT = "\u2039"  # single left angle quote: reads like "<" but is not a tag


def neutralise(text: str) -> str:
    """Defang any `<document` / `</document` so text can't close or open its own wrapper."""
    return _TAG.sub(lambda m: f"{LOOKALIKE_LT}{m.group(1)}{m.group(2)}", text)


def wrap_document(text: str, source: str | None = None) -> str:
    attr = f' source="{neutralise(source).replace(chr(34), "")}"' if source else ""
    return f"<document{attr}>\n{neutralise(text)}\n</document>"
