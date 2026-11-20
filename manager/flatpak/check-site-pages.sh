#!/usr/bin/env bash
# Check a built GitHub Pages site before it is deployed: it has its start page and its 404 page,
# everything those two pages link to or load is in the site, and the start page's Flatpak part is
# on exactly when the site holds the Flatpak repository. The site root returned 404 from the
# start, because docs/ had no index.html (issue #15); this makes that, and any dead link on the two
# pages, a failure.
#
# Run:      manager/flatpak/check-site-pages.sh --full|--docs-only SITE [PREFIX]
#           SITE: a folder laid out by manager/flatpak/build-site.sh (--full: with the Flatpak
#           repository; --docs-only: built with --docs-only, or docs/ itself as Pages serves it
#           from main:/docs). PREFIX: the path the site is served under, /foxreach/ by default.
# Checks:   - SITE/index.html and SITE/404.html exist and have a <title>;
#           - every href and src on them (comments do not count) is either an https link to
#             another site, or a file in SITE: a relative URL from index.html, a PREFIX/... path
#             from either page. 404.html may use PREFIX/... paths only: Pages serves it for any
#             missing address at any depth (PREFIX/a/b/c), where a relative URL would point
#             elsewhere. A #fragment must name an id on the same page;
#           - nothing is loaded from another site (stylesheets, icons, images, scripts);
#           - --full: SITE has repo/, dev.nilltadios.FoxreachManager.flatpakref,
#             foxreach.flatpakrepo and icon.svg; index.html links to the .flatpakref, and holds no
#             "flatpak:begin"/"flatpak:end" line and no @BASE@ or @FINGERPRINT@;
#             --docs-only: SITE has none of those files, and neither page links to or names one.
# Needs:    python3 (PYTHON=/path/to/python to use another).
# Expected: "ok: SITE (full|docs-only): ..." and exit 0; otherwise one "FAIL: ..." line per
#           problem, naming the page, the line and the missing target, and exit 1.
set -euo pipefail
usage() { echo "usage: $0 --full|--docs-only SITE [PREFIX]" >&2; exit 2; }
(( $# == 2 || $# == 3 )) || usage
case $1 in --full) mode=full ;; --docs-only) mode=docs-only ;; *) usage ;; esac
site=$2
prefix=${3:-/foxreach/}
[[ $prefix == /*/ || $prefix == / ]] || { echo "PREFIX must start and end with /: $prefix" >&2; exit 2; }
[[ -d $site ]] || { echo "FAIL: no site folder at $site" >&2; exit 1; }
py=${PYTHON:-python3}
command -v "$py" >/dev/null || { echo "Missing tool: $py" >&2; exit 2; }

# Python's own HTML parser, so that the commented-out Flatpak part is skipped the way a browser
# skips it, which a pattern search over the file would not do.
"$py" -I - "$mode" "$(realpath -- "$site")" "$prefix" <<'PY'
import html.parser, os, sys, urllib.parse

mode, site, prefix = sys.argv[1:4]
flatpak_files = ["repo", "dev.nilltadios.FoxreachManager.flatpakref", "foxreach.flatpakrepo", "icon.svg"]
problems = []
counts = {"local": 0, "external": 0}

def fail(msg):
    problems.append(msg)

class Page(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls, self.local, self.ids, self.text, self.title, self.in_title = [], [], set(), [], "", False
    def handle_starttag(self, tag, attrs):
        line = self.getpos()[0]
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        for name in ("href", "src", "srcset", "poster", "action", "formaction", "data"):
            if a.get(name) is not None:
                self.urls.append((line, tag, name, a[name].strip()))
        if "url(" in (a.get("style") or ""):
            fail(f"{self.page} line {line}: style attribute with url(), which this check cannot follow")
        if tag == "title":
            self.in_title = True
    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
    def handle_data(self, data):
        self.text.append(data)
        if self.in_title:
            self.title += data

def target(page, rel):
    """The file in the site that the local URL rel names, or None when it is not there."""
    path = urllib.parse.unquote(rel)
    if path.endswith("/") or path == "":
        path += "index.html"
    full = os.path.realpath(os.path.join(site, path))
    if full != site and not full.startswith(site + os.sep):
        return None
    if os.path.isdir(full) and os.path.isfile(os.path.join(full, "index.html")):
        return full
    return full if os.path.isfile(full) else None

pages = {}
for page in ("index.html", "404.html"):
    file = os.path.join(site, page)
    if not os.path.isfile(file):
        fail(f"{page} is missing: the site would answer {'its root' if page == 'index.html' else 'a missing address'} without it")
        continue
    p = Page()
    p.page = page
    with open(file, encoding="utf-8") as f:
        source = f.read()
    p.feed(source)
    p.close()
    p.source = source
    pages[page] = p
    if not p.title.strip():
        fail(f"{page} has no <title>")
    if "@import" in source or "url(" in "".join(p.text):
        fail(f"{page}: @import or url() in its styles, which this check cannot follow")
    for line, tag, attr, url in p.urls:
        where = f"{page} line {line}: <{tag} {attr}=\"{url}\">"
        if attr == "srcset":
            fail(f"{where}: srcset is not checked here, use src")
            continue
        parts = urllib.parse.urlsplit(url)
        if parts.scheme in ("http", "https") or url.startswith("//"):
            counts["external"] += 1
            if tag != "a":
                fail(f"{where} loads a file from another site; the site must not call any third party")
            elif parts.scheme != "https":
                fail(f"{where} is not https")
            continue
        if parts.scheme or parts.netloc:
            fail(f"{where}: unexpected kind of URL")
            continue
        if url.startswith("#"):
            if url[1:] and url[1:] not in p.ids:
                fail(f"{where}: no id \"{url[1:]}\" on this page")
            continue
        if parts.path.startswith("/"):
            if not parts.path.startswith(prefix):
                fail(f"{where} is outside the site, which is served under {prefix}")
                continue
            rel = parts.path[len(prefix):]
        elif page == "404.html":
            fail(f"{where} is relative, but 404.html is served at any depth: use {prefix}...")
            continue
        else:
            rel = parts.path
        counts["local"] += 1
        p.local.append(rel)
        if target(page, rel) is None:
            fail(f"{where} points at {rel or 'the site root'}, which the site does not have")

def linked(p, name):
    """Whether the page links to or loads SITE/name, or something inside it."""
    return any(rel == name or rel.startswith(name + "/") for rel in p.local)

present = [n for n in flatpak_files if os.path.exists(os.path.join(site, n))]
if mode == "full":
    for n in flatpak_files:
        if n not in present:
            fail(f"--full, but the site has no {n}")
    p = pages.get("index.html")
    if p:
        lines = p.source.splitlines()
        for marker in ("<!-- flatpak:begin", "flatpak:end -->"):
            if marker in lines:
                fail(f"index.html: the Flatpak part is still a comment (a \"{marker}\" line is left); build-site.sh did not switch it on")
        for ph in ("@BASE@", "@FINGERPRINT@"):
            if ph in p.source:
                fail(f"index.html: {ph} is not filled in")
        if not linked(p, "dev.nilltadios.FoxreachManager.flatpakref"):
            fail("index.html links to no dev.nilltadios.FoxreachManager.flatpakref: the Flatpak part is not on")
else:
    for n in present:
        fail(f"--docs-only, but the site has {n}")
    for page, p in pages.items():
        shown = "".join(p.text)
        for n in flatpak_files:
            # The page's text may say "repo" or "icon.svg" in passing; the two install files'
            # names appear only in the Flatpak part.
            if linked(p, n) or (n.startswith(("dev.", "foxreach.")) and n in shown):
                fail(f"{page} links to or names {n}, which a site without the Flatpak repository does not have")

for msg in problems:
    print(f"FAIL: {msg}", file=sys.stderr)
if problems:
    sys.exit(1)
print(f"ok: {site} ({mode}): index.html and 404.html present, {counts['local']} local links and assets found in the site, "
      f"{counts['external']} links to other sites (not fetched here), nothing loaded from another site")
PY
