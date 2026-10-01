"""Shared contract base: run metadata, field-size helpers and the token-cap check."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from r2r.llm.tokens import estimate_tokens

Role = Literal[
    "retriever",
    "long_planner",
    "short_planner",
    "coordinator",
    "math_designer",
    "coder",
    "optimizer",
    "digest",
    "writer",
    "harness",
    "human",
    "cli",
]

Id = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")]
MilestoneId = Annotated[str, Field(pattern=r"^M\d{1,3}$")]
CriterionId = Annotated[str, Field(pattern=r"^AC\d{1,3}$")]


def Text(max_chars: int, min_chars: int = 0) -> Any:
    """A string field limited to `max_chars` characters."""
    return Annotated[str, Field(min_length=min_chars, max_length=max_chars)]


def Lines(max_lines: int, max_chars: int) -> Any:
    """A string field limited to `max_lines` lines and `max_chars` characters."""

    def check(v: str) -> str:
        if len(v.splitlines()) > max_lines:
            raise ValueError(f"at most {max_lines} lines allowed")
        return v

    return Annotated[str, Field(max_length=max_chars), AfterValidator(check)]


class ContractTooLarge(Exception):
    """A contract's token estimate exceeds its cap in `budgets.yaml`."""

    def __init__(self, contract: str, tokens: int, cap: int) -> None:
        self.contract, self.tokens, self.cap = contract, tokens, cap
        super().__init__(f"{contract} is ~{tokens} tokens, cap is {cap}")


_caps: dict[str, int] | None = None


def set_contract_caps(caps: Mapping[str, int] | None) -> None:
    """Override the caps (tests); `None` reloads them from `budgets.yaml` on next use."""
    global _caps
    _caps = dict(caps) if caps is not None else None


def get_contract_caps() -> dict[str, int]:
    global _caps
    if _caps is None:
        from r2r.settings import load_settings

        _caps = dict(load_settings().budgets.contract_token_caps)
    return _caps


class Part(BaseModel):
    """A value nested inside contracts: strict, immutable, no run metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Contract(Part):
    """Anything passed between agents or graph nodes."""

    milestone_scoped: ClassVar[bool] = False

    created_by: Role
    run_id: Id
    milestone_id: MilestoneId | None = None

    def token_estimate(self, model: str | None = None) -> int:
        return estimate_tokens(self.model_dump_json(), model)

    @model_validator(mode="after")
    def _check_contract(self) -> Self:
        if self.milestone_scoped and self.milestone_id is None:
            raise ValueError(f"{type(self).__name__} requires milestone_id")
        # ContractTooLarge is not a ValueError, so it propagates unwrapped.
        ensure_within_cap(self)
        return self


def ensure_within_cap[C: Contract](contract: C, model: str | None = None) -> C:
    """Raise `ContractTooLarge` if `contract` exceeds its cap, counted with `model`'s tokenizer."""
    name = type(contract).__name__
    cap = get_contract_caps().get(name)
    if cap is not None:
        tokens = contract.token_estimate(model)
        if tokens > cap:
            raise ContractTooLarge(name, tokens, cap)
    return contract
