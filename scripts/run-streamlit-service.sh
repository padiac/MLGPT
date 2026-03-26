#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.local/bin:${HOME}/.cursor/bin:${PATH}"
# Set by systemd Environment=, EnvironmentFile (.env), or default below
export MLGPT_CWD="${MLGPT_CWD:-$ROOT}"
exec streamlit run app.py
