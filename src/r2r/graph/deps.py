"""Per-run dependencies for graph nodes. Kept out of graph state (and so out of checkpoints);
`resume` rebuilds them from `runs/<id>/run.json` in a fresh process."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from r2r.harness.sandbox import DockerBackend, SandboxBackend
from r2r.harness.workspace import Workspace
from r2r.llm.client import LLMClient
from r2r.llm.gateway import CallContext
from r2r.retrieval.vectors import VectorIndex
from r2r.settings import Settings
from r2r.store.blobs import BlobStore
from r2r.store.ledger import Ledger
from r2r.store.runs import RunPaths, run_paths


@dataclass
class RunDeps:
    run_id: str
    settings: Settings
    client: LLMClient
    paths: RunPaths
    proposal_dir: Path
    prompts_dir: Path
    retry_wait_max_s: float = 20.0
    # Docker in production; the framework tests inject an unisolated backend (D29).
    sandbox_factory: Callable[[Workspace], SandboxBackend] | None = None
    # set by open_graph: the run's earlier states, newest first (publishing reads past milestones)
    state_history: Callable[[], Iterable[Mapping[str, Any]]] | None = None
    # D95: approved milestones are copied here (e.g. the owner's project folder)
    export_dir: Path | None = None
    ledger: Ledger = field(init=False)
    blobs: BlobStore = field(init=False)
    index: VectorIndex = field(init=False)

    def __post_init__(self) -> None:
        self.ledger = Ledger(self.paths.ledger)
        self.blobs = BlobStore(self.paths.blobs)
        self.index = VectorIndex(self.paths.vectors, self.ctx())

    def workspace(self) -> Workspace:
        return Workspace.create(self.paths)

    def sandbox(self) -> SandboxBackend:
        return self.sandbox_for(self.workspace())

    def sandbox_for(self, ws: Workspace) -> SandboxBackend:
        if self.sandbox_factory is not None:
            return self.sandbox_factory(ws)
        return DockerBackend(ws, self.settings.budgets.sandbox)

    def ctx(self, milestone_id: str | None = None, briefing: str | None = None) -> CallContext:
        return CallContext(
            run_id=self.run_id,
            milestone_id=milestone_id,
            settings=self.settings,
            ledger=self.ledger,
            client=self.client,
            prompts_dir=self.prompts_dir,
            retry_wait_max_s=self.retry_wait_max_s,
            briefing=briefing,
        )

    @classmethod
    def create(
        cls,
        run_id: str,
        proposal_dir: Path,
        settings: Settings,
        client: LLMClient,
        runs_base: Path,
        prompts_dir: Path,
        retry_wait_max_s: float = 20.0,
        sandbox_factory: Callable[[Workspace], SandboxBackend] | None = None,
        export_dir: Path | None = None,
    ) -> RunDeps:
        paths = run_paths(run_id, base=runs_base, create=True)
        meta = {
            "run_id": run_id,
            "proposal_dir": str(Path(proposal_dir).resolve()),
            "created": datetime.now(UTC).isoformat(),
        }
        if export_dir is not None:
            meta["export_dir"] = str(Path(export_dir).expanduser().resolve())
        (paths.root / "run.json").write_text(json.dumps(meta, indent=2))
        return cls(
            run_id,
            settings,
            client,
            paths,
            Path(proposal_dir),
            prompts_dir,
            retry_wait_max_s,
            sandbox_factory,
            export_dir=Path(meta["export_dir"]) if "export_dir" in meta else None,
        )

    @classmethod
    def load(
        cls,
        run_id: str,
        settings: Settings,
        client: LLMClient,
        runs_base: Path,
        prompts_dir: Path,
        retry_wait_max_s: float = 20.0,
        sandbox_factory: Callable[[Workspace], SandboxBackend] | None = None,
    ) -> RunDeps:
        paths = run_paths(run_id, base=runs_base)
        meta_file = paths.root / "run.json"
        if not meta_file.is_file():
            raise FileNotFoundError(f"no run {run_id!r} under {runs_base / 'runs'}")
        meta = json.loads(meta_file.read_text())
        return cls(
            run_id,
            settings,
            client,
            paths,
            Path(meta["proposal_dir"]),
            prompts_dir,
            retry_wait_max_s,
            sandbox_factory,
            export_dir=Path(meta["export_dir"]) if meta.get("export_dir") else None,
        )
