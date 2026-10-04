"""`r2r extend` (D89): a finished run takes a new proposal and grows new milestones.

The new proposal is ingested into the same run (a new ResearchBrief; identical chunks are not
re-indexed), the run's proposal folder is updated, and the graph continues at the long planner
with a note to keep every finished milestone. The workspace, its frozen checks and its history
stay as they are, so new milestones build on the verified code.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

from r2r.agents.retriever import run_retriever
from r2r.graph.deps import RunDeps
from r2r.graph.driver import config, open_graph
from r2r.graph.routing import next_milestone
from r2r.ingest.proposal import ingest_proposal

NOTE = (
    "The owner extended this study with a new proposal (in `proposal`). Keep every finished "
    "milestone exactly as it is and add new milestones after them for the new scope only. The "
    "finished code is in the workspace with its frozen checks: build on it, never redo it."
)


class ExtendError(RuntimeError):
    pass


def extend(deps: RunDeps, proposal_dir: Path) -> RunDeps:
    """Prepare `deps.run_id` to continue with the proposal in `proposal_dir`; returns the deps
    to drive it with (`drive(..., start=False)`)."""
    proposal_dir = Path(proposal_dir).resolve()
    if not (proposal_dir / "proposal.md").is_file():
        raise ExtendError(f"{proposal_dir}/proposal.md not found")
    cfg = config(deps.run_id)
    with open_graph(deps) as graph:
        values = graph.get_state(cfg).values
    roadmap = values.get("roadmap")
    if roadmap is None:
        raise ExtendError(f"{deps.run_id} has no roadmap yet: use `r2r resume`")
    if next_milestone(roadmap) is not None:
        raise ExtendError(f"{deps.run_id} has milestones still open: finish them with `r2r resume`")
    meta_file = deps.paths.root / "run.json"
    meta = json.loads(meta_file.read_text())
    meta.setdefault("extensions", []).append(
        {"from": meta["proposal_dir"], "to": str(proposal_dir), "at": datetime.now(UTC).isoformat()}
    )
    meta["proposal_dir"] = str(proposal_dir)
    meta_file.write_text(json.dumps(meta, indent=2))
    deps = dataclasses.replace(deps, proposal_dir=proposal_dir)
    ctx = deps.ctx()
    proposal = ingest_proposal(proposal_dir, deps.blobs, deps.index, ctx)
    brief = run_retriever(proposal, deps.blobs, deps.paths, ctx).brief
    update = {
        "status": "running",
        "proposal": proposal,
        "brief": brief,
        "human_note": NOTE,
        "spec": None,
        "verdict": None,
        "math_spec": None,
        "split_request": False,
        "publication": [],
        "publication_md": None,
        "publish_note": None,
        "publish_revisions": 0,
    }
    with open_graph(deps) as graph:
        graph.update_state(cfg, update, as_node="retrieve")  # retrieve → long_plan
    return deps
