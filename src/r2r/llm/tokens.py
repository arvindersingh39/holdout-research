"""estimate_tokens(text, model) with a len/4 fallback.

Tokenizers differ by provider, so callers pass the target role's model whenever they know it.
"""

from __future__ import annotations

import math


def estimate_tokens(text: str, model: str | None = None) -> int:
    """Token count of `text` for `model`; `ceil(len/4)` when no model is given or counting fails."""
    if model:
        try:
            import litellm  # deferred: slow import, and only needed for model-specific counts

            return int(litellm.token_counter(model=model, text=text))
        except Exception:
            pass
    return math.ceil(len(text) / 4)
