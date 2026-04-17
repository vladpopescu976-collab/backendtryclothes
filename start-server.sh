#!/usr/bin/env bash
set -euo pipefail

export HF_HOME="${HF_HOME:-/tmp/huggingface}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-$HF_HOME/hub}"
export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-$HF_HOME/transformers}"
export DIFFUSERS_CACHE="${DIFFUSERS_CACHE:-$HF_HOME/diffusers}"
export TORCH_HOME="${TORCH_HOME:-/tmp/torch}"
export TMPDIR="${TMPDIR:-/tmp}"

mkdir -p "$HF_HOME" "$HF_HUB_CACHE" "$TRANSFORMERS_CACHE" "$DIFFUSERS_CACHE" "$TORCH_HOME" "$TMPDIR"

PORT="${PORT:-8000}"

exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
