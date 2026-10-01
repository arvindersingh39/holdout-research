"""Verify every configured model ID and its API key with one minimal request each.

Calls LiteLLM directly because the gateway doesn't exist yet (Phase 0 only). Each distinct
model gets a single prompt capped at a few output tokens, so a full run costs well under
$0.01. `--dry-run` makes no calls and only shows LiteLLM's price entries.

    uv run python scripts/check_models.py [--dry-run]
"""

from __future__ import annotations

import sys
from collections import defaultdict

import litellm
from rich.console import Console
from rich.table import Table

from r2r.settings import Settings, load_settings

PROMPT = [{"role": "user", "content": "Reply with the single word OK."}]
KEY_FOR_PROVIDER = {"anthropic": "anthropic_api_key", "gemini": "gemini_api_key",
                    "openrouter": "openrouter_api_key"}  # fmt: skip


def models_to_roles(settings: Settings) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for role, cfg in settings.models.roles.items():
        out[cfg.model].append(role)
        if cfg.escalate_to:
            out[cfg.escalate_to].append(f"{role} (escalation)")
    if settings.models.embeddings.model:
        out[settings.models.embeddings.model].append("embeddings")
    return out


def price_entry(model: str) -> str:
    try:
        info = litellm.get_model_info(model)
    except Exception:
        return "no entry"
    inp, outp = info.get("input_cost_per_token"), info.get("output_cost_per_token")
    if inp is None or outp is None:
        return "no prices"
    return f"${inp * 1e6:.2f} in / ${outp * 1e6:.2f} out per Mtok"


def ping(model: str, settings: Settings) -> tuple[bool, str]:
    key_field = KEY_FOR_PROVIDER.get(model.split("/", 1)[0])
    key = getattr(settings.secrets, key_field) if key_field else None
    if key_field and key is None:
        return False, f"{key_field.upper()} not set"
    try:
        if model == settings.models.embeddings.model:
            litellm.embedding(model=model, input=["ok"], api_key=key.get_secret_value())
            return True, "OK"
        resp = litellm.completion(
            model=model, messages=PROMPT, max_tokens=5, timeout=60,
            api_key=key.get_secret_value() if key else None,
        )  # fmt: skip
    except Exception as e:
        # Exception text never contains the key, but keep it short.
        return False, f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
    cost = litellm.completion_cost(completion_response=resp) or 0.0
    return True, f"OK (${cost:.6f})"


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    settings = load_settings()
    console = Console()
    table = Table(title="r2r model check" + (" (dry run, no calls)" if dry_run else ""))
    for col in ("model", "roles", "result", "LiteLLM price entry"):
        table.add_column(col)
    failures = 0
    for model, roles in models_to_roles(settings).items():
        if dry_run:
            result = "skipped"
        else:
            ok, result = ping(model, settings)
            failures += not ok
            result = f"[green]{result}[/]" if ok else f"[red]{result}[/]"
        table.add_row(model, ", ".join(roles), result, price_entry(model))
    console.print(table)
    if not settings.models.embeddings.model:
        console.print("[yellow]embeddings.model is not chosen yet (Phase 0 decision).[/]")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
