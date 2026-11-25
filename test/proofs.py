#!/usr/bin/env python3
"""The Lean proofs in proofs/: they build, leave nothing unproved, and rest on Lean's own axioms only.

Run:      uv run --python 3.15 test/proofs.py [folder]     (folder: another tree laid out like proofs/)
Expected: every line PASS, exit 0 (about 20 s; the first run also downloads the Lean that
          proofs/lean-toolchain names).
How:      a copy of the folder without its build output is built from scratch in a throwaway folder
          (lake build), so a stale build cannot hide a broken proof and the checkout is not written
          to. Audit.lean is then run in that copy: it prints the axioms each theorem depends on.
          The proofs' table of tools (Foxreach/Tools.lean) is compared with the tools the code has
          (bridge/tools.mjs, extension/background.js): a tool the model does not know fails here.
          Last, gaps are planted in copies (an unproved theorem, an assumption, a theorem left out
          of the audit, a wrong proof, a tool taken out of the table): each must be reported, or
          these checks check nothing.
          Needs lake, on PATH or in ~/.elan/bin (https://lean-lang.org/install/). Run it with
          your own HOME, not a throwaway one: Lean's toolchains are kept there.
Not shown: that the model in proofs/Foxreach behaves as the JavaScript does. Lean proves statements
          about the model; test/e2e.py, test/security.py, test/browser_open.py and test/untrusted.py
          are what tie the model's rules to the code.
"""
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROOFS = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "proofs"
LAKE = shutil.which("lake") or str(pathlib.Path.home() / ".elan/bin/lake")
# What every Lean proof may rest on: the three axioms of Lean's own library.
ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
# Ways to state something without proving it, or to take the check away from Lean's kernel.
BANNED = {
    "sorry": r"\bsorry\b",
    "admit": r"\badmit\b",
    "axiom": r"^\s*(?:(?:private|protected|noncomputable|unsafe)\s+)*axiom\b",
    "native_decide": r"\bnative_decide\b",
    "unsafe": r"\bunsafe\b",
    "implemented_by": r"\bimplemented_by\b",
    "extern": r"@\[\s*extern\b",
    "skipKernelTC": r"skipKernelTC",
}
fails = 0


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print("PASS" if ok else "FAIL", name, "" if ok else detail)


def code_of(text):
    """Lean source without its comments (-- to the end of the line, and /- ... -/, which nest)."""
    out, i, depth = [], 0, 0
    while i < len(text):
        two = text[i:i + 2]
        if two == "/-":
            depth += 1
            i += 2
        elif two == "-/" and depth:
            depth -= 1
            i += 2
        elif depth:
            out.append("\n" if text[i] == "\n" else "")
            i += 1
        elif two == "--":
            while i < len(text) and text[i] != "\n":
                i += 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def sources(folder):
    return sorted(p for p in folder.rglob("*.lean") if ".lake" not in p.parts)


def theorems(folder):
    """The full names of the theorems other files can name (not private), by their namespaces."""
    names = set()
    for path in sources(folder):
        space = []
        for line in code_of(path.read_text()).splitlines():
            opened = re.match(r"\s*namespace\s+(\S+)", line)
            closed = re.match(r"\s*end\s+(\S+)", line)
            stated = re.match(r"\s*(?:@\[[^\]]*\]\s*)*(?:(?:protected|noncomputable)\s+)*theorem\s+(\S+)", line)
            if opened:
                space.append(opened.group(1))
            elif closed and space and space[-1] == closed.group(1):
                space.pop()
            elif stated:
                names.add(".".join([*space, stated.group(1)]))
    return names


def tools(folder):
    """The tool names the proofs' table covers, those the MCP server offers, and the add-on's own."""
    table = code_of((folder / "Foxreach/Tools.lean").read_text())
    names = table[table.index("def SimpleTool.name"):table.index("def SimpleTool.request")]
    modelled = set(re.findall(r'=>\s*"([a-z_]+)"', names)) | {"batch"}  # batch is Tool.batch
    offered = set(re.findall(r'(?m)^\s*tool\("([a-z_]+)"', (ROOT / "bridge/tools.mjs").read_text()))
    background = (ROOT / "extension/background.js").read_text()
    own = background[background.index("\nconst TOOLS = {"):background.index("\n};", background.index("\nconst TOOLS = {"))]
    addon = set(re.findall(r"(?m)^  async ([a-z_]+)\(", own))
    return modelled, offered, addon


def lake(folder, *args):
    r = subprocess.run([LAKE, *args], cwd=folder, capture_output=True, text=True, timeout=900)
    return r.returncode, r.stdout + r.stderr


def examine(folder):
    """What is wrong with a proofs tree, by kind; every list is empty for a sound one. The tree is
    built where it is, so pass a copy."""
    found = {"source": [], "build": [], "sorry": [], "warnings": [], "unanswered": [], "axioms": [], "unlisted": []}
    for path in sources(folder):
        for n, line in enumerate(code_of(path.read_text()).splitlines(), 1):
            for what, pattern in BANNED.items():
                if re.search(pattern, line):
                    found["source"].append(f"{path.relative_to(folder)}:{n}: {what}")
    audit = code_of((folder / "Audit.lean").read_text())
    asked = set(re.findall(r"^\s*#print\s+axioms\s+(\S+)", audit, re.M))
    found["unlisted"] = sorted(theorems(folder) - asked)
    status, out = lake(folder, "build")
    found["sorry"] = [line for line in out.splitlines() if re.search(r"declaration uses .sorry.", line)]
    found["warnings"] = [line for line in out.splitlines() if "warning:" in line and line not in found["sorry"]]
    if status != 0:
        found["build"] = [line for line in out.splitlines() if "error" in line][:8] or [f"lake build: exit {status}"]
        return found
    status, out = lake(folder, "env", "lean", "Audit.lean")
    answered = {}
    for name, axioms in re.findall(r"'([^']+)' depends on axioms: \[([^\]]*)\]", out):
        answered[name] = {a.strip() for a in axioms.split(",")}
    for name in re.findall(r"'([^']+)' does not depend on any axioms", out):
        answered[name] = set()
    found["unanswered"] = sorted(asked - set(answered)) + ([] if status == 0 else [f"lean Audit.lean: exit {status}"])
    found["axioms"] = sorted(f"{name}: {', '.join(sorted(axioms - ALLOWED_AXIOMS))}" for name, axioms in answered.items() if axioms - ALLOWED_AXIOMS)
    return found


def planted(built, work, name, lean, audit=""):
    """What examine finds in a copy of the built tree with lean added to the last module of the
    namespace Foxreach, and audit added to Audit.lean."""
    copy = work / name
    shutil.copytree(built, copy)
    module = copy / "Foxreach/Repaired.lean"
    text = module.read_text()
    end = text.rindex("end Foxreach")
    module.write_text(text[:end] + lean + "\n\n" + text[end:])
    with open(copy / "Audit.lean", "a") as f:
        f.write(audit + "\n")
    return examine(copy)


def main():
    check("lake is installed", pathlib.Path(LAKE).is_file(), f"{LAKE}: install Lean with elan, https://lean-lang.org/install/")
    if fails:
        return 1
    toolchain = (PROOFS / "lean-toolchain").read_text().strip()
    check("the Lean version is one fixed release", re.fullmatch(r"leanprover/lean4:v\d+\.\d+\.\d+", toolchain) is not None, toolchain)
    packages = json.loads((PROOFS / "lake-manifest.json").read_text())["packages"]
    check("the proofs use no package beyond Lean itself", packages == [], packages)

    with tempfile.TemporaryDirectory(prefix="foxreach-proofs-") as d:
        work = pathlib.Path(d)
        built = work / "proofs"
        shutil.copytree(PROOFS, built, ignore=shutil.ignore_patterns(".lake"))
        found = examine(built)
        count = len(theorems(built))
        check("nothing in the sources skips a proof or the kernel's check (sorry, axiom, native_decide, unsafe...)", not found["source"], found["source"])
        check("the proofs build from scratch", not found["build"], found["build"])
        check("no declaration is left unproved", not found["sorry"], found["sorry"])
        check("the build gives no warning", not found["warnings"], found["warnings"][:8])
        check(f"Audit.lean names every theorem other files can use ({count})", count > 0 and not found["unlisted"], found["unlisted"])
        check("Lean reports the axioms of every theorem Audit.lean names", not found["unanswered"], found["unanswered"])
        check("every theorem rests on Lean's own axioms only (propext, Classical.choice, Quot.sound)", not found["axioms"], found["axioms"])
        if fails or PROOFS != ROOT / "proofs":
            return 1 if fails else 0

        modelled, offered, addon = tools(built)
        check(f"the proofs' table of tools names the tools the MCP server offers, no more and no fewer ({len(offered)})",
              len(offered) > 20 and modelled == offered, f"not in the table: {sorted(offered - modelled)}; only in the table: {sorted(modelled - offered)}")
        check(f"every tool of the add-on is one of those ({len(addon)})", len(addon) > 20 and addon <= offered, sorted(addon - offered))

        # The same checks on copies with a gap planted: each must see it.
        f = planted(built, work, "unproved", "theorem planted_unproved : (1 : Nat) = 2 := by sorry", "#print axioms Foxreach.planted_unproved")
        check("a planted unproved theorem is reported: in the sources, by the build, and by its axioms",
              any("sorry" in s for s in f["source"]) and f["sorry"] and any("planted_unproved: sorryAx" in a for a in f["axioms"]), f)
        f = planted(built, work, "assumed", "axiom planted_assumption : (1 : Nat) = 2\ntheorem planted_assumed : (1 : Nat) = 2 := planted_assumption",
                    "#print axioms Foxreach.planted_assumed")
        check("a planted assumption is reported: in the sources, and by the theorem that rests on it",
              any("axiom" in s for s in f["source"]) and any("planted_assumed: Foxreach.planted_assumption" in a for a in f["axioms"]), f)
        f = planted(built, work, "unlisted", "theorem planted_unlisted : (1 : Nat) = 1 := rfl")
        check("a planted theorem that Audit.lean does not name is reported", f["unlisted"] == ["Foxreach.planted_unlisted"], f)
        f = planted(built, work, "wrong", "theorem planted_wrong : (1 : Nat) = 2 := rfl", "#print axioms Foxreach.planted_wrong")
        check("a planted wrong proof fails the build", bool(f["build"]), f)
        table = work / "unlisted/Foxreach/Tools.lean"  # any copy will do: the table is only read
        table.write_text(table.read_text().replace('  | .record _ => "record"\n', ""))
        modelled, offered, _ = tools(work / "unlisted")
        check("a tool taken out of the table is reported", offered - modelled == {"record"}, sorted(offered - modelled))
    return 1 if fails else 0


if __name__ == "__main__":
    status = main()
    print("failed:", fails)
    sys.exit(status)
