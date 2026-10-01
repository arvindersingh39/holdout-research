"""Helpers shared by agents: drafts the model fills, and fields code fills in afterwards."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, create_model

from r2r.contracts import Contract, ensure_within_cap
from r2r.llm.gateway import CallContext

META = frozenset({"created_by", "run_id", "milestone_id"})


def draft_model(contract: type[Contract], exclude: set[str] | frozenset[str]) -> type[BaseModel]:
    """`contract` minus `exclude` and run metadata: what the model is asked to return."""
    skip = META | set(exclude)
    return create_model(  # type: ignore[call-overload]
        f"{contract.__name__}Draft",
        __config__=ConfigDict(extra="forbid"),
        **{n: (f.annotation, f) for n, f in contract.model_fields.items() if n not in skip},
    )


def finalize[C: Contract](
    contract: type[C], draft: BaseModel, role: str, ctx: CallContext, **filled: Any
) -> C:
    """Build the full contract from a draft plus code-filled fields; enforce its token cap."""
    data = {
        **draft.model_dump(),
        "created_by": role,
        "run_id": ctx.run_id,
        "milestone_id": ctx.milestone_id,
        **filled,
    }
    return ensure_within_cap(contract.model_validate(data), ctx.settings.models.roles[role].model)
