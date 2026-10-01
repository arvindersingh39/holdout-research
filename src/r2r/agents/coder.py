"""#5 Coder: implements the MathSpec through tools until the frozen tests pass (Phase 5).

The coder can read anything in the workspace but write only under src/ and bench/. Attempts to
write tests or checks are refused, logged and counted; the harness verifies the frozen hash
before every test run regardless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from r2r.contracts import MathSpec, MilestoneSpec, TestResult
from r2r.harness.frozen import FrozenTamper
from r2r.harness.sandbox import SandboxBackend
from r2r.harness.tests import run_tests
from r2r.harness.workspace import FROZEN_DIRS, UnsafePath, Workspace, safe_path
from r2r.llm.gateway import CallContext, run_tool_loop
from r2r.llm.tools import Tool, ToolLoopResult, ToolLoopState
from r2r.log import get_logger

log = get_logger("r2r.agents.coder")

ROLE = "coder"
WRITE_DIRS = ("src", "bench")
READ_LINES = 400
CREATE_LINES = 600
_HIDDEN = {".r2r", "__pycache__", ".pytest_cache"}


class ListArgs(BaseModel):
    dir: str = "."


class ReadArgs(BaseModel):
    path: str
    start: int = 1
    end: int | None = None


class EditArgs(BaseModel):
    path: str
    old: str
    new: str


class CreateArgs(BaseModel):
    path: str
    content: str


class NoArgs(BaseModel):
    pass


@dataclass
class CoderTools:
    ws: Workspace
    sandbox: SandboxBackend
    run_id: str
    milestone_id: str
    timeout_s: float
    tamper_attempts: list[str] = field(default_factory=list)
    last_result: TestResult | None = None

    def tools(self) -> list[Tool]:
        return [
            Tool(
                "list_files",
                "List workspace files under a directory.",
                ListArgs,
                self.list_files,
                max_output_chars=4000,
            ),
            Tool(
                "read_file",
                f"Read lines start..end of a file (at most {READ_LINES} lines).",
                ReadArgs,
                self.read_file,
                max_output_chars=24_000,
            ),
            Tool(
                "edit_file",
                "Replace the unique occurrence of `old` with `new` in a file under src/ or bench/.",
                EditArgs,
                self.edit_file,
                max_output_chars=500,
            ),
            Tool(
                "create_file",
                f"Create or overwrite a file under src/ or bench/ (at most {CREATE_LINES} lines).",
                CreateArgs,
                self.create_file,
                max_output_chars=500,
            ),
            Tool(
                "run_tests",
                "Run the frozen tests and return the TestResult as JSON.",
                NoArgs,
                self.run_tests,
                max_output_chars=6000,
            ),
        ]

    # --- read ---------------------------------------------------------------------------

    def _readable(self, rel: str) -> Path:
        p = Path(rel)
        root = self.ws.root.resolve()
        resolved = (root / p).resolve()
        if p.is_absolute() or ".." in p.parts or not resolved.is_relative_to(root):
            raise UnsafePath(f"path must stay inside the workspace: {rel!r}")
        return resolved

    def list_files(self, args: ListArgs) -> str:
        base = self._readable(args.dir)
        root = self.ws.root.resolve()
        files = sorted(
            p.relative_to(root).as_posix()
            for p in base.rglob("*")
            if p.is_file() and not _HIDDEN & set(p.relative_to(root).parts)
        )
        return "\n".join(files) or "(no files)"

    def read_file(self, args: ReadArgs) -> str:
        lines = self._readable(args.path).read_text().splitlines()
        start = max(1, args.start)
        end = min(len(lines), args.end or len(lines), start + READ_LINES - 1)
        body = "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(start, end + 1))
        more = f"\n[... {len(lines) - end} more lines]" if end < len(lines) else ""
        return f"{args.path} ({len(lines)} lines)\n{body}{more}"

    # --- write ---------------------------------------------------------------------------

    def _writable(self, rel: str) -> Path:
        try:
            return safe_path(self.ws.root, rel, WRITE_DIRS)
        except UnsafePath:
            norm = Path(rel).as_posix().lstrip("./")
            if any(norm == d or norm.startswith(f"{d}/") for d in FROZEN_DIRS):
                self.tamper_attempts.append(rel)
                log.warning("tamper attempt blocked", extra={"path": rel})
                raise PermissionError(
                    f"{rel}: tests/frozen/ and checks/ are read-only. "
                    "Change the code, not the tests."
                ) from None
            raise

    def edit_file(self, args: EditArgs) -> str:
        path = self._writable(args.path)
        text = path.read_text()
        count = text.count(args.old)
        if count == 0:
            raise ValueError(f"`old` not found in {args.path}")
        if count > 1:
            raise ValueError(f"`old` appears {count} times in {args.path}; make it unique")
        path.write_text(text.replace(args.old, args.new, 1))
        return f"edited {args.path}"

    def create_file(self, args: CreateArgs) -> str:
        n = len(args.content.splitlines())
        if n > CREATE_LINES:
            raise ValueError(f"file has {n} lines; at most {CREATE_LINES} lines per file")
        path = self._writable(args.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args.content)
        return f"wrote {args.path} ({n} lines)"

    # --- test --------------------------------------------------------------------------------

    def run_tests(self, _: NoArgs) -> str:
        try:
            result = run_tests(
                self.ws,
                self.sandbox,
                run_id=self.run_id,
                milestone_id=self.milestone_id,
                timeout_s=self.timeout_s,
            )
        except FrozenTamper as e:
            self.tamper_attempts.append(str(e))
            raise
        self.last_result = result
        return result.model_dump_json(exclude={"created_by", "run_id", "milestone_id"})


class CoderInput(BaseModel):
    spec: MilestoneSpec
    math_spec: MathSpec
    last_test_result: TestResult | None = None
    files: str


@dataclass(frozen=True)
class CoderSession:
    loop: ToolLoopResult
    tools: CoderTools


def run_coder_session(
    spec: MilestoneSpec,
    math_spec: MathSpec,
    last: TestResult | None,
    tools: CoderTools,
    ctx: CallContext,
    model: str | None = None,
) -> CoderSession:
    def stop(_: ToolLoopState) -> bool:
        return tools.last_result is not None and tools.last_result.passed

    payload = CoderInput(
        spec=spec, math_spec=math_spec, last_test_result=last, files=tools.list_files(ListArgs())
    )
    max_turns = ctx.settings.budgets.coder_max_turns
    loop = run_tool_loop(ROLE, ROLE, payload, tools.tools(), max_turns, stop, ctx, model=model)
    return CoderSession(loop=loop, tools=tools)
