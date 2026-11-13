#!/usr/bin/env bash
# Check `foxreach install` against the real agents it supports, in a throwaway Podman container:
# OpenClaw, GitHub Copilot CLI, Qwen Code, Kimi Code, MiMo Code, MiniMax Code, Mistral Vibe and
# DeepSeek Harness. inside.sh installs each from its official source, runs `foxreach agents add`,
# and confirms with each agent's own tools that it reads foxreach's entry.
#
# Run:      bash test/agents_container/run.sh [LOGFILE]     (needs podman and network; ~5 min)
# Expected: Qwen and MiMo report foxreach connected; Copilot and OpenClaw list it (OpenClaw's probe
#           finds the tools); dsh's composed profile contains mcp-foxreach; Vibe removes foxreach
#           by name; Kimi's and MiniMax's code read mcp.json from the folders foxreach writes to.
# Isolation: --rm container; only read-only copies of this checkout and Bun are mounted; SELinux
#           labelling is off for this container so no host file is relabelled.
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
BUN=${BUN:-$(command -v bun || echo "$HOME/.bun/bin/bun")}
IMAGE=${IMAGE:-docker.io/library/node:24.22.1-bookworm}
podman run --rm --name foxreach-agents-check --security-opt label=disable \
  -v "$ROOT:/foxreach:ro" \
  -v "$BUN:/usr/local/bin/bun:ro" \
  -v "$ROOT/test/agents_container/inside.sh:/inside.sh:ro" \
  "$IMAGE" bash /inside.sh 2>&1 | tee "${1:-/dev/null}"
