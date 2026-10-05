"""D93: benchmarks. The retriever's values are quote-checked by code, stored with the owner's
recorded values, shown to the coder without their numbers, and compared in plain Python with the
results file the owner produces by running the reproduce script."""

import json
from pathlib import Path

import pytest

from r2r.contracts import Benchmark
from r2r.harness.benchmarks import (
    BenchmarkFileError,
    check_results,
    coder_view,
    expected_refs,
    load_reference,
    outline,
    results_file,
    verify_benchmarks,
    write_benchmarks,
)

PROPOSAL = (
    "On the 20-graph ensemble the static allocation reaches an attack rate of 0.412 "
    "(Table 2), and the threshold sits at B = 138.5 units."
)


def bm(i: int, value: float, quote: str, tol: float = 0.0005, source: str = "proposal"):
    return Benchmark(
        id=f"BM{i}",
        quantity=f"quantity {i}",
        value=value,
        units="",
        tolerance=tol,
        conditions="20 graphs",
        source=source,
        quote=quote,
    )


def test_quotes_are_checked_against_the_source_text() -> None:
    out = verify_benchmarks(
        [
            bm(1, 0.412, "reaches an attack rate of 0.412"),
            bm(2, 138.5, "threshold  sits at B = 138.5 units"),  # whitespace differs: fine
            bm(3, 0.5, "reaches an attack rate of 0.412"),  # value not in the quote
            bm(4, 0.412, "an attack rate of 0.412 in every run"),  # quote not in the text
            bm(5, 41.2, "attack rate of 0.412", source="smith2020"),  # unknown source: all texts
        ],
        {"proposal": PROPOSAL},
    )
    assert [b.verified for b in out] == [True, True, False, False, False]


def test_percent_values_match_their_fraction() -> None:
    (b,) = verify_benchmarks(
        [bm(1, 0.27, "cut by 27% overall")], {"proposal": "cut by 27% overall"}
    )
    assert b.verified


def _run(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    (data / "recorded_values.json").write_text(
        json.dumps(
            {
                "graph12": {"kappa": 25.73, "edges": 112},
                "ensemble": {"ar": [0.31, 0.29]},
                "tolerances": {"graph12.kappa": 0.05, "ensemble.ar[*]": 0.01},
            }
        )
    )
    write_benchmarks(tmp_path, [bm(1, 0.412, "q", tol=0.0005), bm(2, 138.5, "q", tol=0.05)])
    return tmp_path


def test_reference_merges_owner_data_and_benchmarks(tmp_path: Path) -> None:
    ref = load_reference(_run(tmp_path))
    assert ref["benchmarks"] == {"BM1": 0.412, "BM2": 138.5}
    assert ref["tolerances"]["benchmarks.BM1"] == 0.0005
    assert ref["tolerances"]["graph12.kappa"] == 0.05
    assert ref["about"]["BM1"]["quantity"] == "quantity 1"


def test_expected_refs_expand_prefixes_to_leaves(tmp_path: Path) -> None:
    ref = load_reference(_run(tmp_path))
    got = expected_refs(ref, ["graph12", "ensemble.ar", "benchmarks.BM2"])
    assert got == [
        "graph12.kappa",
        "graph12.edges",
        "ensemble.ar[0]",
        "ensemble.ar[1]",
        "benchmarks.BM2",
    ]
    with pytest.raises(KeyError):
        expected_refs(ref, ["nowhere"])


def test_coder_sees_refs_and_descriptions_never_values(tmp_path: Path) -> None:
    ref = load_reference(_run(tmp_path))
    view = coder_view(ref, ["benchmarks.BM1", "graph12.kappa"])
    assert view[0] == {
        "ref": "benchmarks.BM1",
        "quantity": "quantity 1",
        "units": "",
        "conditions": "20 graphs",
    }
    assert view[1] == {"ref": "graph12.kappa"}
    text = json.dumps(view)
    assert "0.412" not in text and "25.73" not in text


def test_outline_lists_refs_for_the_planner(tmp_path: Path) -> None:
    text = outline(load_reference(_run(tmp_path)))
    assert "benchmarks.BM1" in text and "quantity 1" in text and "ensemble.ar" in text
    assert "tolerances" not in text


def _results(tmp_path: Path, entries: list) -> Path:
    p = tmp_path / "out.json"
    p.write_text(json.dumps({"entries": [{"ref": r, "generated": g} for r, g in entries]}))
    return p


def test_results_within_tolerance_are_verified(tmp_path: Path) -> None:
    ref = load_reference(_run(tmp_path))
    f = _results(tmp_path, [("benchmarks.BM1", 0.4123), ("graph12.kappa", 25.7)])
    rep = check_results(f, ref, ["benchmarks.BM1", "graph12.kappa"], run_id="r", milestone_id="M1")
    assert rep.status == "verified" and rep.n_within == 2


def test_mismatch_and_missing_refs(tmp_path: Path) -> None:
    ref = load_reference(_run(tmp_path))
    f = _results(tmp_path, [("benchmarks.BM1", 0.5)])
    rep = check_results(f, ref, ["benchmarks.BM1"], run_id="r", milestone_id="M1")
    assert rep.status == "mismatch" and rep.mismatches[0].recorded == 0.412
    rep = check_results(f, ref, ["benchmarks.BM1", "graph12"], run_id="r", milestone_id="M1")
    assert (
        rep.status == "error" and "graph12.kappa" in rep.log_tail and "not reported" in rep.log_tail
    )


def test_copied_values_are_flagged(tmp_path: Path) -> None:
    ref = load_reference(_run(tmp_path))
    f = _results(
        tmp_path, [("ensemble.ar[0]", 0.31), ("ensemble.ar[1]", 0.29), ("graph12.kappa", 25.73)]
    )
    rep = check_results(f, ref, ["ensemble", "graph12.kappa"], run_id="r", milestone_id="M1")
    assert rep.status == "error" and "copied" in rep.log_tail


def test_bad_results_files(tmp_path: Path) -> None:
    ref = load_reference(_run(tmp_path))
    with pytest.raises(BenchmarkFileError, match="not found"):
        check_results(
            tmp_path / "nope.json", ref, ["benchmarks.BM1"], run_id="r", milestone_id="M1"
        )
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(BenchmarkFileError, match="entries"):
        check_results(bad, ref, ["benchmarks.BM1"], run_id="r", milestone_id="M1")


def test_results_file_path_from_the_owner(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    assert results_file(ws, "M3", "") == ws / "results/benchmarks/m3.json"
    assert results_file(ws, "M3", "  out/r.json ") == ws / "out/r.json"
    assert results_file(ws, "M3", "/abs/r.json") == Path("/abs/r.json")
    with pytest.raises(BenchmarkFileError, match="json"):
        results_file(ws, "M3", "r.csv")


def test_an_extension_adds_benchmarks_after_the_existing_ones(tmp_path: Path) -> None:
    """D93 with `r2r extend`: the new proposal's results get fresh ids; earlier ones stay."""
    write_benchmarks(tmp_path, [bm(1, 0.412, "q"), bm(2, 138.5, "q")])
    added = write_benchmarks(tmp_path, [bm(1, 7.0, "new q")], keep_existing=True)
    assert [b.id for b in added] == ["BM3"]
    ref = load_reference(tmp_path)
    assert ref["benchmarks"] == {"BM1": 0.412, "BM2": 138.5, "BM3": 7.0}
    assert ref["about"]["BM3"]["quote"] == "new q"
