"""Stand-in for `radon raw -j DIR` in the unit-test backend only (stdlib; radon lives in the
sandbox image). Prints {file: {"sloc": n}} counting non-blank, non-comment lines."""

import json
import sys
from pathlib import Path


def main() -> None:
    out = {}
    for p in sorted(Path(sys.argv[-1]).rglob("*.py")):
        lines = [ln.strip() for ln in p.read_text().splitlines()]
        out[p.as_posix()] = {"sloc": sum(1 for ln in lines if ln and not ln.startswith("#"))}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
