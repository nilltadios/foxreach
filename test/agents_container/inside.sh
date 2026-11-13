#!/usr/bin/env bash
# Runs INSIDE a throwaway container (see run.sh). Installs each agent from its official source,
# adds foxreach with `foxreach agents add`, then checks with each agent's own tools that it reads
# foxreach's entry. Agents without a "list" command are checked another way (noted per agent).
set -u
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
npm config set prefix "$HOME/.npm-global" >/dev/null
FOX="bun /foxreach/bridge/cli.mjs"
step() { printf '\n===== %s\n' "$*"; }
try() { timeout "${T:-240}" "$@" 2>&1 | tail -n "${LINES_KEPT:-25}"; }

step "install"
curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
T=900 try npm install -g @qwen-code/qwen-code @github/copilot openclaw @deepseek-ai/dsh
T=600 try uv tool install --python 3.12 mistral-vibe           # 3.14 has no wheel yet
T=600 try bash -c 'curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash'
T=600 try bash -c 'curl -fsSL https://mimo.xiaomi.com/install | bash'
T=600 try bash -c 'curl -fsSL https://filecdn.minimax.chat/public/install.sh | bash'
export PATH="$HOME/.kimi-code/bin:$HOME/.mimocode/bin:$HOME/.minimax-code/bin:$PATH"
step "on PATH"
for c in qwen copilot openclaw dsh vibe kimi mimo mcode; do printf '%-9s %s\n' "$c" "$(command -v $c || echo MISSING)"; done

step "foxreach agents add"; LINES_KEPT=40 try $FOX agents add

step "Qwen Code: qwen mcp list (connects to the server)";       try qwen mcp list
step "GitHub Copilot CLI: copilot mcp list";                    try copilot mcp list
step "OpenClaw: openclaw mcp list";                             try openclaw mcp list
step "OpenClaw: openclaw mcp probe foxreach (starts it, counts tools)"; try openclaw mcp probe foxreach
step "MiMo Code: mimo mcp list (needs a project folder)"
mkdir -p /tmp/project && cd /tmp/project && git init -q && try mimo mcp list; cd /
step "DeepSeek Harness: dsh --profile web --dump-config (foxreach's patch in the composed profile)"
timeout 300 dsh --profile web --dump-config 2>&1 | grep -n -A8 'id: mcp-foxreach' | head -12
step "Mistral Vibe (no list command): vibe adds its own stdio entry, then removes foxreach's by name"
try vibe mcp add vibe-check --transport stdio --command /usr/local/bin/bun --arg /foxreach/bridge/cli.mjs --arg mcp
cat ~/.vibe/config.toml
try vibe mcp remove foxreach
step "Kimi Code (no mcp command): where its code reads user MCP servers"
strings "$(readlink -f "$(command -v kimi)")" | grep -o 'user: join.\{0,80\}mcp\.json"[)]' | sort -u | head -3
step "MiniMax Code (no list command): where its code reads MCP servers"
root=$(cd "$(dirname "$(readlink -f "$(command -v mcode)")")/.." && pwd)
grep -rhoa --include='*.js' 'filePath(){return [A-Za-z]*(this.dataDir(),"mcp.json")}' "$root" | sort -u | head -3
