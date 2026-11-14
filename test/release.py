#!/usr/bin/env python3
"""The release path, end to end, in a throwaway copy of this repository: nothing is published.

Run:      uv run --python 3.15 test/release.py
Expected: every line PASS, "N passed, 0 failed", exit 0. About a minute; the first run downloads
          Bun's runtime for each target this machine is not.

Copies the work tree as it is now (uncommitted changes included) into a temporary clone, commits
there, prepares a release with scripts/release.py, tags it, and has scripts/verify_release.py
compare the release's files with a rebuild from the tag. Then it damages the files one way at a
time and expects verify_release.py to say so. install.sh is run against those files, with a
throwaway home folder.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
results = []
# macOS has shasum where Linux has sha256sum; both read and write the same SHA256SUMS lines.
SHA256 = ["sha256sum"] if shutil.which("sha256sum") else ["shasum", "-a", "256"]
HOSTS = ("Library/Application Support/Mozilla/NativeMessagingHosts" if sys.platform == "darwin"
         else ".mozilla/native-messaging-hosts")


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" -- {detail}"), flush=True)


def run(*command, cwd, env=None):
    return subprocess.run([str(c) for c in command], cwd=cwd, capture_output=True, text=True, env=env)


def main():
    with tempfile.TemporaryDirectory(prefix="foxreach-release-") as d:
        work = pathlib.Path(d)
        repo = work / "repo"
        subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(repo)], check=True)
        changed = subprocess.run(["git", "ls-files", "-m", "-o", "-d", "--exclude-standard"], cwd=ROOT, check=True,
                                 capture_output=True, text=True).stdout.split("\n")
        for name in filter(None, changed):
            if (ROOT / name).exists():
                (repo / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, repo / name)
            else:
                (repo / name).unlink(missing_ok=True)
        manifest = repo / "extension/manifest.json"
        ver = "9.9.9"  # no such release exists, so nothing here can be mistaken for one
        manifest.write_text(manifest.read_text().replace(f'"version": "{json.loads(manifest.read_text())["version"]}"', f'"version": "{ver}"'))
        git = lambda *args: subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],  # noqa: E731
                                           cwd=repo, check=True, capture_output=True, text=True).stdout
        git("add", "-A")
        git("commit", "-q", "-m", "Work tree under test")

        py = ("uv", "run", "--python", "3.15")
        dirty = repo / "untracked.txt"
        dirty.write_text("x")
        check("a release is refused while the work tree has changes", "work tree has changes" in run(*py, "scripts/release.py", cwd=repo).stderr)
        dirty.unlink()
        # A signed build, as Mozilla returns it: the same files and a META-INF folder.
        made = run(*py, "python", "-c", "import sys; sys.path.insert(0, 'scripts'); import sign; sign.SIGNED.mkdir(parents=True, exist_ok=True); "
                   "sign.build_store(sign.signed_path(), 'unlisted')", cwd=repo)
        signed = repo / f"dist/signed/foxreach-{ver}-signed.xpi"
        with zipfile.ZipFile(signed, "a") as z:
            z.writestr("META-INF/mozilla.rsa", b"stand-in for a signature")
        out = run(*py, "scripts/release.py", cwd=repo)
        release = repo / "dist/release"
        names = sorted(p.name for p in release.iterdir()) if release.exists() else []
        check("release.py prepares the add-on, both programs, the installer and the checksums",
              out.returncode == 0 and names == sorted(["BUILDINFO.txt", "SHA256SUMS", f"foxreach-{ver}.xpi", f"foxreach-{ver}-signed.xpi",
                                                       f"foxreach-{ver}-unsigned.xpi", "foxreach-linux-arm64", "foxreach-linux-x64",
                                                       "foxreach-darwin-arm64", "foxreach-darwin-x64", "install.sh"]),
              (names, made.stderr, out.stdout, out.stderr))
        updates = json.loads((repo / "docs/updates.json").read_text())["addons"]["foxreach@foxreach.local"]["updates"]
        signed_updates = json.loads((repo / "docs/updates-signed.json").read_text())["addons"]["foxreach@foxreach.local"]["updates"]
        check("installed add-ons are offered the new version, each build from its own list",
              updates[-1]["version"] == ver and updates[-1]["update_link"].endswith(f"foxreach-{ver}.xpi")
              and signed_updates[-1]["update_link"].endswith(f"foxreach-{ver}-signed.xpi"), (updates[-1], signed_updates))
        with zipfile.ZipFile(signed) as z:
            gecko = json.loads(z.read("manifest.json"))["browser_specific_settings"]["gecko"]
        check("the signed build looks for updates in the signed list", gecko.get("update_url", "").endswith("/updates-signed.json"), gecko)
        sums = run(*SHA256, "-c", "SHA256SUMS", cwd=release)
        check("SHA256SUMS checks out with sha256sum", sums.returncode == 0, sums.stdout + sums.stderr)

        git("add", "docs")
        git("commit", "-q", "-m", f"Release {ver}")
        git("tag", f"v{ver}")
        verify = lambda: run(*py, "scripts/verify_release.py", ver, "--dir", release, cwd=repo)  # noqa: E731
        ok = verify()
        check("verify_release.py rebuilds the tag and finds every file the same",
              ok.returncode == 0 and "8 files match" in ok.stdout and "DIFFERS" not in ok.stdout and "skipped" not in ok.stdout, ok.stdout + ok.stderr)
        again = run(*py, "scripts/release.py", "--files-only", work / "ci", cwd=repo)
        check("a second build elsewhere, as the release workflow does it, gives the same checksums",
              again.returncode == 0 and (work / "ci/SHA256SUMS").read_text() == (release / "SHA256SUMS").read_text(), again.stderr)

        def damaged(name, change):
            """verify_release.py's output after one file was changed and its checksum line refreshed."""
            keep = {n: (release / n).read_bytes() for n in (name, "SHA256SUMS")}
            change(release / name)
            fresh = run(*SHA256, name, cwd=release).stdout
            (release / "SHA256SUMS").write_text("".join(fresh if line.split()[1] == name else line + "\n"
                                                        for line in keep["SHA256SUMS"].decode().splitlines()))
            result = verify()
            for n, data in keep.items():
                (release / n).write_bytes(data)
            return result

        def add_entry(path):
            with zipfile.ZipFile(path, "a") as z:
                z.writestr("extra.js", "fetch('https://example.invalid')")

        bad = damaged("foxreach-linux-x64", lambda p: p.write_bytes(p.read_bytes() + b"x"))
        check("a changed program is reported", bad.returncode == 1 and "FAILED   foxreach-linux-x64" in bad.stdout, bad.stdout)
        bad = damaged(f"foxreach-{ver}-signed.xpi", add_entry)
        check("a signed add-on with a file the source does not have is reported", bad.returncode == 1 and "extra.js" in bad.stdout, bad.stdout)
        bad = damaged(f"foxreach-{ver}.xpi", add_entry)
        check("a changed add-on is reported", bad.returncode == 1 and f"FAILED   foxreach-{ver}.xpi" in bad.stdout, bad.stdout)

        home = work / "home"
        home.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")} | {
            "HOME": str(home), "FOXREACH_RELEASE_URL": f"file://{release}"}
        installed = run("sh", repo / "install.sh", "--no-flatpak", cwd=work, env=env)
        program = home / ".local/bin/foxreach"
        check("install.sh installs the program for this machine and registers the host",
              installed.returncode == 0 and program.exists() and (home / HOSTS / "foxreach.json").exists()
              and run(program, "version", cwd=work).stdout.strip() == ver, installed.stdout + installed.stderr)
        if sys.platform == "darwin" and run("arch", "-x86_64", "/usr/bin/true", cwd=work).returncode == 0:
            # Under Rosetta, uname says x86_64: install.sh takes the Intel build, whose signature is
            # Bun's and no longer matches, and must sign it for this Mac.
            program.unlink()
            intel = run("arch", "-x86_64", "sh", repo / "install.sh", "--no-flatpak", cwd=work, env=env)
            check("install.sh on an Intel Mac (Rosetta here) signs the x64 build so it runs",
                  intel.returncode == 0 and "foxreach-darwin-x64" in intel.stdout
                  and run("codesign", "-v", program, cwd=work).returncode == 0
                  and run("arch", "-x86_64", program, "version", cwd=work).stdout.strip() == ver, intel.stdout + intel.stderr)
        (release / "SHA256SUMS").write_text((release / "SHA256SUMS").read_text().replace("0", "1"))
        program.unlink()
        refused = run("sh", repo / "install.sh", "--no-flatpak", cwd=work, env=env)
        check("install.sh installs nothing when the checksum is wrong", refused.returncode == 1 and not program.exists() and "Checksum mismatch" in refused.stderr, refused.stderr)

    failed = results.count(False)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
