"""refs.bib from PaperCards, without an LLM."""

import re

from samples import paper_card

from r2r.contracts import Citation, PaperCard
from r2r.ingest.citations import to_bibtex


def _card(**citation) -> PaperCard:
    base = paper_card()
    cit = Citation(**{**base.citation.model_dump(), **citation})
    return PaperCard.model_validate({**base.model_dump(), "citation": cit.model_dump()})


def test_basic_entry() -> None:
    bib = to_bibtex([paper_card()])
    assert bib.startswith("@book{glasserman2003,")
    assert "title = {Monte Carlo Methods}" in bib
    assert "author = {Paul Glasserman}" in bib
    assert "year = {2003}" in bib
    assert bib.count("{") == bib.count("}")


def test_escaping_and_venue_field() -> None:
    card = _card(entry_type="article", title="Risk & Return: 5% of $ #1_x",
                 authors=["A. One", "B. Two"], venue="J. Fin. & Econ.", doi="10.1/x")  # fmt: skip
    bib = to_bibtex([card])
    assert r"Risk \& Return: 5\% of \$ \#1\_x" in bib
    assert "author = {A. One and B. Two}" in bib
    assert r"journal = {J. Fin. \& Econ.}" in bib
    assert "doi = {10.1/x}" in bib


def test_duplicate_keys_get_suffixes() -> None:
    bib = to_bibtex([paper_card(), paper_card(), paper_card()])
    keys = re.findall(r"^@\w+\{([^,]+),", bib, re.M)
    assert keys == ["glasserman2003", "glasserman2003-b", "glasserman2003-c"]


def test_empty() -> None:
    assert to_bibtex([]) == ""
