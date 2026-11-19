#!/usr/bin/env python3
"""Set the icon of the add-on's page on addons.mozilla.org.

Run:      uv run --python 3.15 scripts/amo_listing.py --dry-run     show what would be sent; sends nothing
          uv run --python 3.15 scripts/amo_listing.py               upload amo/icon-128.png as the listing icon
          uv run --python 3.15 scripts/amo_listing.py --render      redraw amo/icon-128.png from
                                                                    extension/icons/icon.svg (needs Inkscape)
Needs:    the same API key as scripts/sign.py (environment, or ~/.config/foxreach/amo.env, mode 600);
          the key is never printed. --dry-run and --render need no key.
Sends:    one request, PATCH https://addons.mozilla.org/api/v5/addons/addon/<add-on id>/ with the PNG
          as the multipart field "icon" (AMO's add-on Edit API: square JPEG or PNG, 128x128
          recommended; AMO makes its 32, 64 and 128 px copies a little later). Nothing else changes.
Expected: exit 0; the real mode prints the icon URL AMO now reports.
Undo:     upload another icon the same way, or remove it on the add-on's "Edit Product Page" (Media).

Why: AMO never takes the listing icon from the add-on's manifest. It shows its default icon until
an author uploads one, in the Developer Hub (Edit Product Page, Media) or through this API. The
add-on itself keeps its SVG icon.
"""
import argparse
import base64
import hashlib
import hmac
import json
import pathlib
import secrets
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from sign import credentials  # noqa: E402

API = "https://addons.mozilla.org/api/v5"
ADDON_ID = "foxreach@foxreach.local"
SVG = ROOT / "extension/icons/icon.svg"
ICON = ROOT / "amo/icon-128.png"
SIZE = 128


def render():
    inkscape = shutil.which("inkscape")
    if not inkscape:
        raise SystemExit("Inkscape was not found; it draws the PNG from the SVG.")
    command = [inkscape, str(SVG), "--export-type=png", f"--export-filename={ICON}", f"--export-width={SIZE}", f"--export-height={SIZE}"]
    print(" ".join(command))
    subprocess.run(command, check=True, capture_output=True)


def png_size(path):
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise SystemExit(f"{path.relative_to(ROOT)} is not a PNG.")
    return struct.unpack(">II", data[16:24])


def token(key, secret):
    """The JWT AMO's API expects: HMAC-SHA256, issuer = API key, a nonce, at most 5 minutes long."""
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=")  # noqa: E731
    now = int(time.time())
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = b64(json.dumps({"iss": key, "jti": secrets.token_hex(16), "iat": now, "exp": now + 60}, separators=(",", ":")).encode())
    signature = b64(hmac.new(secret.encode(), head + b"." + body, hashlib.sha256).digest())
    return (head + b"." + body + b"." + signature).decode()


def multipart(field, filename, content_type, data):
    boundary = "foxreach-" + secrets.token_hex(12)
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--render", action="store_true")
    args = parser.parse_args()
    if args.render:
        render()
        print(f"{ICON.relative_to(ROOT)}: {'x'.join(map(str, png_size(ICON)))}")
        return 0
    width, height = png_size(ICON)
    if width != height or width < SIZE:
        raise SystemExit(f"{ICON.relative_to(ROOT)} is {width}x{height}; AMO needs a square icon, {SIZE}x{SIZE} recommended.")
    data = ICON.read_bytes()
    url = f"{API}/addons/addon/{urllib.parse.quote(ADDON_ID, safe='')}/"
    body, content_type = multipart("icon", ICON.name, "image/png", data)
    print(f"PATCH {url}")
    print(f"  Content-Type: multipart/form-data; field \"icon\" = {ICON.relative_to(ROOT)} "
          f"(image/png, {width}x{height}, {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()})")
    print("  Authorization: JWT <signed with the API key; not shown>")
    if args.dry_run:
        print("dry run: nothing was sent.")
        return 0
    keys = credentials()
    request = urllib.request.Request(url, data=body, method="PATCH", headers={
        "Content-Type": content_type,
        "Authorization": "JWT " + token(keys["WEB_EXT_API_KEY"], keys["WEB_EXT_API_SECRET"]),
    })
    try:
        with urllib.request.urlopen(request, timeout=60) as r:
            reply = json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"AMO refused it: HTTP {e.code} {e.read().decode(errors='replace')[:500]}")
    print(f"listing icon set: {reply.get('icon_url')} (AMO resizes it shortly; the URL may 404 for a minute)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
