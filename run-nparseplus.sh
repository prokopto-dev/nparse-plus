#!/bin/zsh
# Launch nParse+ natively on macOS. Run alongside the game;
# it tails the EQ log directory configured in nparse.config.json.
cd "$(dirname "$0")"
exec uv run python -m nparseplus "$@"
