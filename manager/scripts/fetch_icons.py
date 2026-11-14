#!/usr/bin/env python3
"""Fetch the brand icons the app bundles and generate src/bundled_icons.rs.

Run:      uv run --python 3.15 manager/scripts/fetch_icons.py
Sources:  two npm packages, each version pinned below and checked by SHA-256:
          - simple-icons (https://github.com/simple-icons/simple-icons, CC0 1.0), filled with each
            brand's colour;
          - @lobehub/icons-static-svg (https://github.com/lobehub/lobe-icons, MIT), for AI brands
            Simple Icons lacks (Grok, OpenAI, OpenClaw, Hermes Agent, Muse).
          The logos are their owners' trademarks; see SOURCES.md for each one.
Writes:   manager/resources/icons/<name>.svg, SOURCES.md beside them, and manager/src/bundled_icons.rs,
          which compiles them into the app.
Expected: one line per icon written, exit 0. The app prefers an icon installed on the system and
          falls back to these; brands not listed here get a coloured initial.
"""
import hashlib
import io
import json
import pathlib
import re
import sys
import tarfile
import urllib.request

SIMPLE_VERSION = "16.37.0"
SIMPLE_URL = f"https://registry.npmjs.org/simple-icons/-/simple-icons-{SIMPLE_VERSION}.tgz"
SIMPLE_SHA256 = "cd9faf0e6faa8a8fdd15e9632ab4c86fcae4b986fe552b87673149847f2f671d"
# Simple Icons slug -> what it stands for in the app.
SIMPLE = {
    "claude": "Claude Code, Claude desktop",
    "google": "Antigravity",
    "cursor": "Cursor",
    "githubcopilot": "GitHub Copilot CLI",
    "mistralai": "Mistral Vibe",
    "deepseek": "DeepSeek Harness",
    "qwen": "Qwen Code",
    "kimi": "Kimi Code",
    "minimax": "MiniMax Code",
    "xiaomi": "MiMo Code",
    "opencode": "opencode",
    "firefoxbrowser": "Firefox",
    "librewolf": "LibreWolf",
    "floorp": "Floorp",
    "zenbrowser": "Zen",
}

LOBE_VERSION = "1.102.0"
LOBE_URL = f"https://registry.npmjs.org/@lobehub/icons-static-svg/-/icons-static-svg-{LOBE_VERSION}.tgz"
LOBE_SHA256 = "ad3f2ab605d59f3968b9ea57c9b2aab941ee3608da72fca57ac6e0ded301f5a2"
# LobeHub icon name -> what it stands for. Icons drawn in currentColor take the theme's text colour;
# "-color" ones keep their own colours.
LOBE = {
    "grok": "Grok Build",
    "openai": "Codex, ChatGPT app",
    "openclaw-color": "OpenClaw",
    "hermesagent": "Hermes",
    "muse-color": "Muse Code",
    "antigravity-color": "Antigravity",
    "codex-color": "Codex",
    "claudecode-color": "Claude Code",
    "xiaomimimo": "MiMo Code",
    "zai": "ZCode (Z.ai)",
}

# Logos taken from a project's own site, pinned by SHA-256: name -> (url, sha256, shown for).
DIRECT = {
    # The Pi coding agent's logo, as its README shows it (https://github.com/badlogic/pi-mono, MIT).
    "pi": ("https://pi.dev/logo.svg", "abd66e7868b2d24f0f0895f9237ee8a6dcb22337583b0dc54aeb595acecb4d6b", "Pi"),
}

MANAGER = pathlib.Path(__file__).resolve().parent.parent
OUT = MANAGER / "resources/icons"


def fetch(url, sha256):
    data = urllib.request.urlopen(url, timeout=60).read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha256:
        raise SystemExit(f"{url}: SHA-256 {got}, expected {sha256}. Nothing written.")
    tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    return lambda name: tar.extractfile(f"package/{name}").read().decode()


def plain(name, svg):
    body = svg.lstrip()
    if body.startswith("<?xml"):
        body = body.split("?>", 1)[1].lstrip()
    if not body.startswith("<svg") or "<script" in svg or "href=" in svg:
        raise SystemExit(f"{name}.svg is not a plain SVG")
    return svg


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows, themed, names = [], set(), []

    read = fetch(SIMPLE_URL, SIMPLE_SHA256)
    meta = json.loads(read("data/simple-icons.json"))
    meta = meta if isinstance(meta, list) else meta["icons"]
    by_slug = {(i.get("slug") or re.sub(r"[^a-z0-9]", "", i["title"].lower())): i for i in meta}
    for slug, use in SIMPLE.items():
        icon = by_slug[slug]
        svg = plain(slug, read(f"icons/{slug}.svg"))
        r, g, b = (int(icon["hex"][i:i + 2], 16) for i in (0, 2, 4))
        if 0.2126 * r + 0.7152 * g + 0.0722 * b < 60:
            themed.add(slug)  # a black logo would vanish on a dark theme
        else:
            svg = svg.replace("<svg ", f'<svg fill="#{icon["hex"]}" ', 1)
        (OUT / f"{slug}.svg").write_text(svg)
        lic = icon.get("license", {}).get("type", "CC0-1.0")
        rows.append(f"| `{slug}.svg` | {icon['title']} | {use} | Simple Icons {SIMPLE_VERSION} | {lic} |")
        names.append(slug)
        print(OUT / f"{slug}.svg")

    read = fetch(LOBE_URL, LOBE_SHA256)
    for name, use in LOBE.items():
        svg = plain(name, read(f"icons/{name}.svg"))
        if 'fill="currentColor"' in svg and not name.endswith("-color"):
            themed.add(name)
        (OUT / f"{name}.svg").write_text(svg)
        rows.append(f"| `{name}.svg` | {name} | {use} | LobeHub icons {LOBE_VERSION} | MIT |")
        names.append(name)
        print(OUT / f"{name}.svg")

    for name, (url, sha256, use) in DIRECT.items():
        data = urllib.request.urlopen(url, timeout=60).read()
        got = hashlib.sha256(data).hexdigest()
        if got != sha256:
            raise SystemExit(f"{url}: SHA-256 {got}, expected {sha256}. Nothing written.")
        (OUT / f"{name}.svg").write_text(plain(name, data.decode()))
        rows.append(f"| `{name}.svg` | {use} | {use} | {url} (SHA-256 `{sha256[:16]}…`) | the project's own logo |")
        names.append(name)
        print(OUT / f"{name}.svg")

    (OUT / "SOURCES.md").write_text(
        "# Bundled icons\n\nWritten by `manager/scripts/fetch_icons.py` from two pinned npm packages:\n\n"
        f"- [Simple Icons](https://github.com/simple-icons/simple-icons) {SIMPLE_VERSION} (`{SIMPLE_URL}`, "
        f"SHA-256 `{SIMPLE_SHA256}`), CC0 1.0, filled with each brand's colour;\n"
        f"- [LobeHub icons](https://github.com/lobehub/lobe-icons) {LOBE_VERSION} (`{LOBE_URL}`, "
        f"SHA-256 `{LOBE_SHA256}`), MIT.\n\n"
        "The logos are trademarks of their owners and are used here only to show which app is which. "
        "The app uses an icon installed on the system first.\n\n"
        "| File | Brand | Shown for | Source | License |\n|---|---|---|---|---|\n" + "\n".join(rows) + "\n")
    arms = "\n".join(f'        "{n}" => (include_bytes!("../resources/icons/{n}.svg"), {str(n in themed).lower()}),' for n in names)
    (MANAGER / "src/bundled_icons.rs").write_text(
        "// Generated by manager/scripts/fetch_icons.py; do not edit.\n"
        "/// A bundled brand icon by name: the SVG, and whether it is drawn in the theme's text colour\n"
        "/// (a black or currentColor logo) instead of its own colours.\n"
        "pub fn get(name: &str) -> Option<(&'static [u8], bool)> {\n"
        "    Some(match name {\n" + arms + "\n        _ => return None,\n    })\n}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
