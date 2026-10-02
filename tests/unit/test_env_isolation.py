"""Secrets reach r2r only via `Settings`: importing LiteLLM must not copy `.env` into os.environ.

LiteLLM calls `load_dotenv()` on import unless LITELLM_MODE is set. That leaked every key in `.env`
into the process environment, so tests that load settings with `env_file=None` saw real keys
(order-dependent failures, and a real Tavily client built inside a test).
"""

import subprocess
import sys
from pathlib import Path

PROBE = (
    "import os, r2r.llm.client as c, r2r.llm.tokens\n"
    "import litellm\n"
    "print('LEAK' if 'R2R_PROBE_KEY' in os.environ else 'CLEAN')\n"
)


def test_importing_litellm_does_not_load_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("R2R_PROBE_KEY=not-a-real-secret\n")
    out = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "CLEAN"
