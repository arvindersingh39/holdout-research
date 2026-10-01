# r2r

Turns a research proposal into publish-ready results (a LaTeX paper, a tested Python
package, or both) using a pipeline of LLM agents with human approval gates.

- Plan: `docs/PLAN.md` · Status and decisions: `docs/PROGRESS.md` · Working rules: `CLAUDE.md`

```bash
uv sync
cp .env.example .env        # then fill in ANTHROPIC_API_KEY and GEMINI_API_KEY
uv run pytest -q
uv run python scripts/check_models.py --dry-run   # no API calls
uv run python scripts/check_models.py             # one tiny request per model (< $0.01)
```
