"""Bench runner, executed inside the sandbox (stdlib only; not part of the r2r package).

Usage: python /harness/bench_runner.py bench/bench_main.py REPEATS
Imports the entrypoint, calls main() once to warm up (imports, numba JIT), then times
REPEATS calls with perf_counter and prints one JSON line prefixed with R2R_BENCH.
"""

import importlib.util
import json
import sys
import time
from pathlib import Path


def load_main(path: str):
    sys.path[:0] = [str(Path.cwd() / "src"), str(Path(path).resolve().parent)]
    spec = importlib.util.spec_from_file_location("r2r_bench_target", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main


def main() -> None:
    path, repeats = sys.argv[1], int(sys.argv[2])
    fn = load_main(path)
    fn()
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    print("R2R_BENCH " + json.dumps({"times": times}), flush=True)


if __name__ == "__main__":
    main()
