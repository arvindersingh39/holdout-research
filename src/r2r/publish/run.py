"""One publish pass (Phase 8), shared by the graph's publish node and `r2r publish`."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from r2r.contracts import BlobRef, PaperCard, PublicationBundle
from r2r.harness.sandbox import SandboxBackend
from r2r.harness.workspace import Workspace
from r2r.llm.gateway import CallContext
from r2r.publish.collect import RunRecord
from r2r.publish.package import assemble_package, build_wheel, verify_wheel
from r2r.publish.paper import build_paper
from r2r.store.blobs import BlobStore


def load_cards(record: RunRecord, blobs: BlobStore) -> list[PaperCard]:
    refs = record.brief.paper_cards if record.brief else []
    return [PaperCard.model_validate_json(blobs.get_text(r)) for r in refs]


def run_publish(
    record: RunRecord,
    ws: Workspace,
    out_dir: Path,
    sandbox_for: Callable[[Workspace], SandboxBackend],
    ctx: CallContext,
    blobs: BlobStore,
    modes: Sequence[str],
    *,
    note: str | None = None,
) -> tuple[list[PublicationBundle], str]:
    """Build each mode; return the bundles and the publication gate's markdown."""
    meta = {"run_id": ctx.run_id}
    bundles: list[PublicationBundle] = []
    md: list[str] = [f"## Publication · {record.title or record.roadmap.goal}"]
    if record.partial:
        md.append("**Partial run:** not every planned milestone is done.")
    if "paper" in modes:
        res = build_paper(
            record, load_cards(record, blobs), ws, out_dir, sandbox_for, ctx, note=note
        )
        arts = [_put(blobs, res.tex, "other", "paper.tex")]
        if res.pdf:
            arts.append(_put(blobs, res.pdf, "other", "paper.pdf"))
        arts += [_put(blobs, out_dir / "paper" / f, "figure", f) for f in res.figures]
        bundles.append(
            PublicationBundle(
                created_by="writer",
                mode="paper",
                artifacts=arts,
                build_ok=res.build_ok,
                log_tail=_tail(res.log_tail),
                **meta,
            )
        )
        md += ["", "### Paper", f"- PDF: `{res.pdf}`" if res.pdf else "- **Did not compile.**"]
        md.append(f"- Sections: {', '.join(res.sections)}")
        md.append(f"- Figures: {', '.join(res.figures) or 'none'}")
        if res.unverified:
            md.append("- **Numbers not found in the inputs (check before approving):**")
            md += [f"  - {sec}: {', '.join(nums)}" for sec, nums in res.unverified.items()]
        else:
            md.append("- Every decimal number in the text appears in the run's results.")
        if not res.build_ok:
            md += ["", "```", res.log_tail, "```"]
    if "package" in modes:
        pkg = assemble_package(record, ws, out_dir)
        wheel, build_log = build_wheel(pkg)
        check = verify_wheel(wheel, pkg, sandbox_for, out_dir / "verify") if wheel else None
        ok = bool(check and check.passed)
        arts = [_put(blobs, wheel, "code", wheel.name)] if wheel else []
        bundles.append(
            PublicationBundle(
                created_by="harness",
                mode="package",
                artifacts=arts,
                build_ok=ok,
                log_tail=_tail(check.log_tail if check else build_log),
                **meta,
            )
        )
        md += ["", "### Package", f"- Folder: `{pkg}` (README.md, API.md, CI workflow)"]
        md.append(f"- Wheel: `{wheel}`" if wheel else "- **Wheel did not build.**")
        if check:
            verdict = "pass" if check.passed else "FAIL"
            md.append(
                f"- Installed wheel in a clean sandbox imports: {verdict} "
                f"({check.n_passed} modules imported, {check.n_failed} failed)"
            )
    return bundles, "\n".join(md)


def _put(blobs: BlobStore, path: Path, kind: str, summary: str) -> BlobRef:
    return blobs.put(path.read_bytes(), kind, summary, "writer")  # type: ignore[arg-type]


def _tail(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-40:])[-4000:]
