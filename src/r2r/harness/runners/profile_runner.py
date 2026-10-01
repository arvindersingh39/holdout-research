"""Profile runner, executed inside the sandbox (stdlib only; not part of the r2r package).

Usage: python /harness/profile_runner.py bench/bench_main.py
Warms main() up once, profiles one call with cProfile, and prints JSON (prefixed R2R_PROFILE)
with the functions under src/ by cumulative time. The host parses only this JSON.
"""

import cProfile
import importlib.util
import json
import pstats
import sys
import time
from pathlib import Path

TOP = 20


def load_main(path: str):
    sys.path[:0] = [str(Path.cwd() / "src"), str(Path(path).resolve().parent)]
    spec = importlib.util.spec_from_file_location("r2r_profile_target", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main


def main() -> None:
    fn = load_main(sys.argv[1])
    fn()
    prof = cProfile.Profile()
    t0 = time.perf_counter()
    prof.enable()
    fn()
    prof.disable()
    total = time.perf_counter() - t0
    root = Path.cwd().resolve()
    rows = []
    for (file, line, func), (_cc, _nc, _tt, cum, _callers) in pstats.Stats(prof).stats.items():
        try:
            rel = Path(file).resolve().relative_to(root)
        except ValueError:
            continue
        if rel.parts[:1] == ("src",) and not func.startswith("<"):
            rows.append({"file": rel.as_posix(), "line": line, "func": func, "cum_s": cum})
    rows.sort(key=lambda r: -r["cum_s"])
    print("R2R_PROFILE " + json.dumps({"total_s": total, "rows": rows[:TOP]}), flush=True)


if __name__ == "__main__":
    main()
