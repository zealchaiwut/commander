#!/usr/bin/env python3
"""Build the committed frontend bundle without Node (issue #797).

The canonical build is esbuild (`npm run build`), which CI runs to gate the
source. But the dashboard is served from disk with no build step, so the
*committed* `static/dist/bundle.js` is what production actually loads — and it
must be regenerated whenever a `static/src/` module changes. On machines
without Node installed, this script reproduces esbuild's IIFE output closely
enough for production: it concatenates the ES-module sources in dependency
order, strips `import`/`export` (every cross-module reference is routed through
`window.`, so no import resolution is needed), and wraps the result in a strict
IIFE — byte-for-byte compatible with the `<script src="/static/dist/bundle.js">`
contract in project.html.

Usage:
    python3 scripts/build_frontend_bundle.py
    python3 scripts/build_frontend_bundle.py --check   # verify, exit 1 on drift
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "apps" / "dashboard" / "static" / "src"
DIST_DIR = REPO_ROOT / "apps" / "dashboard" / "static" / "dist"

# Source modules in dependency order. Concern modules first (they define the
# functions and init shared window state), then the barrels that attach the
# public API to `window`. Mirrors what esbuild emits walking imports from
# src/index.js.
MANIFEST = [
    "logpanel.js",
    "sprint-board/state.js",
    "sprint-board/board-render.js",
    "sprint-board/drag-drop.js",
    "sprint-board/run-controls.js",
    "sprint-board/finish-modal.js",
    "sprint-board/rerun-modal.js",
    "sprint-board/index.js",
    "index.js",
]

_IMPORT_START_RE = re.compile(r"^\s*import\b")
_EXPORT_BLOCK_RE = re.compile(r"^\s*export\s*\{[^}]*\}\s*;?\s*$")


def _strip_module_syntax(body: str) -> str:
    """Drop import statements (single- or multi-line) and the `export` keyword;
    every cross-module reference is routed through `window.`, so no import
    binding is needed at runtime — keep everything else verbatim."""
    out = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if _IMPORT_START_RE.match(line):
            # Consume the whole statement, which may span several lines until
            # the one that terminates it with a semicolon.
            while i < len(lines) and not lines[i].rstrip().endswith(";"):
                i += 1
            i += 1  # skip the terminating line
            continue
        if _EXPORT_BLOCK_RE.match(line):
            i += 1
            continue
        line = re.sub(r"^(\s*)export\s+(function|const|let|var|class|async)\b",
                      r"\1\2", line)
        out.append(line)
        i += 1
    return "\n".join(out)


def build() -> str:
    chunks = ['"use strict";', "(() => {"]
    for rel in MANIFEST:
        path = SRC_DIR / rel
        if not path.exists():
            raise SystemExit(f"missing source module: {path}")
        body = _strip_module_syntax(path.read_text(encoding="utf-8")).rstrip("\n")
        indented = "\n".join(("  " + ln if ln else "") for ln in body.splitlines())
        chunks.append(f"  // apps/dashboard/static/src/{rel}")
        chunks.append(indented)
    chunks.append("})();")
    chunks.append("//# sourceMappingURL=bundle.js.map")
    return "\n".join(chunks) + "\n"


def build_sourcemap() -> str:
    """Minimal but valid v3 source map listing the bundled sources."""
    return json.dumps(
        {
            "version": 3,
            "file": "bundle.js",
            "sources": [f"../src/{rel}" for rel in MANIFEST],
            "sourcesContent": [
                (SRC_DIR / rel).read_text(encoding="utf-8") for rel in MANIFEST
            ],
            "mappings": "",
            "names": [],
        },
        separators=(",", ":"),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the committed frontend bundle.")
    ap.add_argument("--check", action="store_true",
                    help="verify the committed bundle matches the sources; exit 1 on drift")
    args = ap.parse_args()

    bundle = build()
    sourcemap = build_sourcemap()
    bundle_path = DIST_DIR / "bundle.js"
    map_path = DIST_DIR / "bundle.js.map"

    if args.check:
        current = bundle_path.read_text(encoding="utf-8") if bundle_path.exists() else ""
        if current != bundle:
            print("bundle.js is out of date — run scripts/build_frontend_bundle.py",
                  file=sys.stderr)
            return 1
        print("bundle.js is up to date")
        return 0

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text(bundle, encoding="utf-8")
    map_path.write_text(sourcemap, encoding="utf-8")
    print(f"wrote {bundle_path.relative_to(REPO_ROOT)} ({len(bundle)} bytes)")
    print(f"wrote {map_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
