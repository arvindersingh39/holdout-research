"""Comparison of reported values with the reference (D75, D84; D93): only the reference's own
values and tolerances count. No LLM involved."""

import pytest

from r2r.harness.compare import RefError, compare_entries, resolve, tolerance_for

REF = {
    "ensemble": {
        "graphs": [
            {"n_stubs": 52, "mu": 1.0, "ok": True},
            {"n_stubs": 162, "mu": 3.05, "ok": False},
        ]
    },
    "graph0": {"B1": [0.001, 0.003]},
    "free_text": "hello",
    "loose": 0.5,
    "tolerances": {"ensemble.graphs[*].mu": 0.005, "graph0.B1[*]": 0.0005},
}


def test_resolve_paths() -> None:
    assert resolve("ensemble.graphs[1].n_stubs", REF) == 162
    assert resolve("graph0.B1[0]", REF) == 0.001
    with pytest.raises(RefError, match="graphs\\[7\\]"):
        resolve("ensemble.graphs[7].mu", REF)
    with pytest.raises(RefError):
        resolve("nope", REF)


def test_tolerance_patterns() -> None:
    tol = REF["tolerances"]
    assert tolerance_for("ensemble.graphs[1].mu", tol) == 0.005
    assert tolerance_for("graph0.B1[1]", tol) == 0.0005
    assert tolerance_for("ensemble.graphs[1].n_stubs", tol) is None


def test_compare_uses_owner_values_and_tolerances() -> None:
    entries = [
        {"ref": "ensemble.graphs[1].mu", "generated": 3.0526},  # within 0.005
        {
            "ref": "ensemble.graphs[0].mu",
            "generated": 1.2,
            "recorded": 1.2,
        },  # report's 'recorded' ignored
        {"ref": "ensemble.graphs[1].n_stubs", "generated": 162},  # int: exact
        {"ref": "ensemble.graphs[0].n_stubs", "generated": 53},  # int mismatch
        {"ref": "ensemble.graphs[1].ok", "generated": False},  # bool: exact
        {"ref": "loose", "generated": 0.51},  # float without tolerance: unchecked, never passed
        {"ref": "free_text", "generated": "hello"},  # string: exact
        {"ref": "missing.key", "generated": 1},  # bad ref: counted as an error
    ]
    c = compare_entries(entries, REF)
    assert (c.n_checked, c.n_within, c.n_mismatched, c.n_unchecked) == (6, 4, 2, 1)
    assert {m.ref for m in c.mismatches} == {"ensemble.graphs[0].mu", "ensemble.graphs[0].n_stubs"}
    assert any("missing.key" in e for e in c.errors)


def test_booleans_and_numbers_never_pass_as_each_other() -> None:
    ref = {"flag": True, "count": 1}
    c = compare_entries([{"ref": "flag", "generated": 1}, {"ref": "count", "generated": True}], ref)
    assert (c.n_checked, c.n_mismatched) == (2, 2)


def test_copied_values_are_an_error() -> None:
    entries = [
        {"ref": "ensemble.graphs[0].mu", "generated": 1.0},
        {"ref": "ensemble.graphs[1].mu", "generated": 3.05},
        {"ref": "graph0.B1[0]", "generated": 0.001},
        {"ref": "graph0.B1[1]", "generated": 0.003},
    ]
    c = compare_entries(entries, REF)
    assert any("copied" in e for e in c.errors)
    computed = [dict(e, generated=e["generated"] + 1e-4) for e in entries]
    assert not any("copied" in e for e in compare_entries(computed, REF).errors)
    stubs = ((0, 52), (1, 162))
    exact_ints = [{"ref": f"ensemble.graphs[{i}].n_stubs", "generated": n} for i, n in stubs]
    assert not compare_entries(exact_ints * 2, REF).errors  # counts legitimately match exactly
