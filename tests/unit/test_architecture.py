"""Architecture rules that are cheap to enforce mechanically."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "r2r"
PROVIDER_IMPORT = re.compile(
    r"^\s*(import|from)\s+(litellm|anthropic|openai|google\.genai)\b", re.M
)


def test_llm_libraries_imported_only_in_llm_package() -> None:
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if "llm" not in p.relative_to(SRC).parts and PROVIDER_IMPORT.search(p.read_text())
    ]
    assert offenders == []


def test_no_print_in_library_code() -> None:
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if p.name != "cli.py"
        and "runners" not in p.parts
        and re.search(r"^\s*print\(", p.read_text(), re.M)
    ]
    assert offenders == []


def test_local_backend_only_in_tests() -> None:
    """D29: the unisolated backend never runs generated code in production."""
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if p.name != "sandbox.py" and "LocalProcessBackend" in p.read_text()
    ]
    assert offenders == []


def test_runners_use_stdlib_only() -> None:
    """Runners execute inside the sandbox, where r2r is not installed."""
    for p in (SRC / "harness" / "runners").glob("*.py"):
        assert not re.search(r"^\s*(import|from)\s+r2r\b", p.read_text(), re.M), p.name
