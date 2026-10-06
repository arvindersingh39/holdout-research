"""#5 Coder: implements the MathSpec's stubs through tools until the stub check passes (D93).

The coder can read anything in the workspace but write only under src/, bench/ and figures/.
The stub check (plain Python) is its finish line: every stub implemented with its signature, the
package importable, and a reproduce script when the milestone has benchmarks. The benchmark values
themselves never reach the coder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from r2r.contracts import MathSpec, MilestoneSpec, TestResult
from r2r.harness.sandbox import SandboxBackend
from r2r.harness.stubcheck import Stub, check_code
from r2r.harness.workspace import UnsafePath, Workspace, safe_path
from r2r.llm.gateway import CallContext, run_tool_loop
from r2r.llm.tools import Tool, ToolLoopResult, ToolLoopState
from r2r.log import get_logger

log = get_logger("r2r.agents.coder")

ROLE = "coder"
WRITE_DIRS = ("src", "bench", "figures", "scripts")
NUDGE = (
    "Continue with the tools: write the code with create_file/edit_file, then call check_code. "
    "The session ends when check_code passes."
)
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


class AppendArgs(BaseModel):
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
    stubs: list[Stub] = field(default_factory=list)
    reproduce: str | None = None  # the reproduce script a benchmark milestone needs
    blocked_writes: list[str] = field(default_factory=list)
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
                "append_file",
                "Append `content` to a file under src/ or bench/ (created if missing). Use it to "
                f"write a big file in parts; each part at most {CREATE_LINES} lines.",
                AppendArgs,
                self.append_file,
                max_output_chars=500,
            ),
            Tool(
                "check_code",
                "Run the stub check: every stub implemented with its signature, the package "
                "importable, and the reproduce script present when needed. Returns a TestResult.",
                NoArgs,
                self.check_code,
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
        path = self._readable(args.path)
        try:
            lines = path.read_text().splitlines()
        except UnicodeDecodeError:  # saved tensors (.pt), arrays, figures: describe, don't decode
            size = path.stat().st_size
            return f"{args.path}: binary file ({size} bytes); inspect it with code, not read_file"
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
            self.blocked_writes.append(rel)
            log.warning("write outside the code folders refused", extra={"path": rel})
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

    def append_file(self, args: AppendArgs) -> str:
        n = len(args.content.splitlines())
        if n > CREATE_LINES:
            raise ValueError(f"part has {n} lines; at most {CREATE_LINES} lines per call")
        path = self._writable(args.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(args.content)
        total = len(path.read_text().splitlines())
        return f"appended {n} lines to {args.path} ({total} lines now)"

    # --- check -------------------------------------------------------------------------------

    def check_code(self, _: NoArgs) -> str:
        result = check_code(
            self.ws,
            self.sandbox,
            self.stubs,
            self.reproduce,
            timeout_s=self.timeout_s,
            run_id=self.run_id,
            milestone_id=self.milestone_id,
        )
        self.last_result = result
        return result.model_dump_json(exclude={"created_by", "run_id", "milestone_id", "frozen_ok"})


class CoderInput(BaseModel):
    spec: MilestoneSpec
    math_spec: MathSpec
    last_check: TestResult | None = None
    files: str
    benchmarks: list[dict[str, str]] = []  # refs to report, with descriptions; never values
    reproduce_script: str | None = None  # write it when there are benchmarks
    results_file: str | None = None  # where the reproduce script writes its entries
    mismatches: str | None = None  # the owner's run disagreed with the reference: fix the code


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
    benchmarks: list[dict[str, str]] | None = None,
    results_file: str | None = None,
    mismatches: str | None = None,
) -> CoderSession:
    def stop(_: ToolLoopState) -> bool:
        return tools.last_result is not None and tools.last_result.passed

    payload = CoderInput(
        spec=spec,
        math_spec=math_spec,
        last_check=last,
        files=tools.list_files(ListArgs()),
        benchmarks=benchmarks or [],
        reproduce_script=tools.reproduce,
        results_file=results_file if tools.reproduce else None,
        mismatches=mismatches,
    )
    max_turns = ctx.settings.budgets.coder_max_turns
    loop = run_tool_loop(
        ROLE, ROLE, payload, tools.tools(), max_turns, stop, ctx, model=model, nudge=NUDGE
    )
    return CoderSession(loop=loop, tools=tools)
