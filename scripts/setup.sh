#!/usr/bin/env bash
# One-command setup after cloning. Safe to run again; it never overwrites your .env.
#
#   ./scripts/setup.sh              install, create .env, run the free offline tests
#   ./scripts/setup.sh --sandbox    also build the Docker sandbox image (about 2 GB)
#   ./scripts/setup.sh --no-tests   skip the tests
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

build_sandbox=false
run_tests=true
for arg in "$@"; do
  case "$arg" in
    --sandbox) build_sandbox=true ;;
    --no-tests) run_tests=false ;;
    -h | --help)
      sed -n 2,6p "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $arg (try --help)" >&2
      exit 2
      ;;
  esac
done

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required and manages Python 3.12 for you. Install it, then run this again:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  echo "  (or on macOS: brew install uv)" >&2
  exit 1
fi

echo "==> Installing Python 3.12 and locked dependencies"
uv sync

if [ -f .env ]; then
  echo "==> Keeping your existing .env"
else
  echo "==> Creating .env from .env.example"
  cp .env.example .env
  chmod 600 .env
fi

docker_up=false
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker_up=true
fi

if [ "$build_sandbox" = true ]; then
  if [ "$docker_up" = false ]; then
    echo "--sandbox needs Docker running. Start Docker and run this again." >&2
    exit 1
  fi
  echo "==> Building the sandbox image (about 2 GB, first time only)"
  uv run r2r sandbox build
fi

if [ "$run_tests" = true ]; then
  echo "==> Running the offline tests (no API calls, no cost)"
  uv run pytest -q
fi

echo
echo "Setup finished. Next steps:"
if ! grep -qE '^ANTHROPIC_API_KEY=.+' .env || ! grep -qE '^OPENROUTER_API_KEY=.+' .env; then
  echo "  1. Add ANTHROPIC_API_KEY and OPENROUTER_API_KEY to .env"
fi
if [ "$docker_up" = false ]; then
  echo "  -  Install or start Docker, which runs the generated code"
fi
if [ "$build_sandbox" = false ]; then
  echo "  -  Build the sandbox:       uv run r2r sandbox build"
fi
echo "  -  Check keys and models:   uv run python scripts/check_models.py    (under \$0.01)"
echo "  -  Run the example:         uv run r2r run --proposal examples/pilot_mc_pricer"
echo
echo "Live runs spend real API credit. Caps are in config/budgets.yaml."
