#!/usr/bin/env bash
# Downloads a standalone, self-contained CPython 3.12 (arm64/aarch64-apple-darwin)
# build from astral-sh/python-build-standalone, then uses its own pip to
# install the vendored `markitdown` package (with the extras this app needs)
# into it.
#
# The result is cached under build/python-runtime so that re-running this
# script is fast when nothing has changed. Set FORCE_REBUILD=1 to ignore the
# cache and rebuild from scratch.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$PROJECT_ROOT/build"
RUNTIME_DIR="$BUILD_DIR/python-runtime"
CACHE_DIR="$BUILD_DIR/.cache"
VENDOR_PKG="$PROJECT_ROOT/engine/vendor/markitdown"
EXTRAS="pptx,docx,xlsx,xls,pdf,outlook"
STAMP_FILE="$RUNTIME_DIR/.markitdown-install-stamp"

PYTHON_SERIES="3.12"
GITHUB_API_URL="https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"

mkdir -p "$BUILD_DIR" "$CACHE_DIR"

# ---------------------------------------------------------------------------
# 1. Compute a stamp of the vendored package so we know whether a previously
#    built runtime is still up to date.
# ---------------------------------------------------------------------------
compute_vendor_stamp() {
    # Hash pyproject.toml + all source files under engine/vendor/markitdown/src.
    # Bytecode caches are excluded: importing the package (e.g. running the
    # regression suite) writes __pycache__ files, which would otherwise change
    # the hash and make an up-to-date runtime look stale, forcing a full
    # network rebuild.
    (
        cd "$PROJECT_ROOT"
        find engine/vendor/markitdown/pyproject.toml engine/vendor/markitdown/src \
            -type f -not -path '*/__pycache__/*' -print0 \
            | sort -z \
            | xargs -0 shasum -a 256 \
            | shasum -a 256 \
            | awk '{print $1}'
    )
}

VENDOR_STAMP="$(compute_vendor_stamp)"

if [[ "${FORCE_REBUILD:-0}" != "1" ]] && [[ -x "$RUNTIME_DIR/bin/python3" ]] && [[ -f "$STAMP_FILE" ]]; then
    EXISTING_STAMP="$(cat "$STAMP_FILE" 2>/dev/null || true)"
    if [[ "$EXISTING_STAMP" == "$VENDOR_STAMP" ]]; then
        echo "[build_python_runtime] Cached runtime at $RUNTIME_DIR is up to date. Skipping."
        exit 0
    fi
    echo "[build_python_runtime] Vendored markitdown changed since last build; rebuilding."
fi

# ---------------------------------------------------------------------------
# 2. Find the latest python-build-standalone release asset for
#    aarch64-apple-darwin, CPython 3.12.x, "install_only" (not stripped, not
#    freethreaded).
# ---------------------------------------------------------------------------
echo "[build_python_runtime] Looking up latest python-build-standalone release..."
RELEASE_JSON="$CACHE_DIR/release.json"
curl -fsSL "$GITHUB_API_URL" -o "$RELEASE_JSON"

ASSET_URL="$(python3 - "$RELEASE_JSON" "$PYTHON_SERIES" <<'PYEOF'
import json
import re
import sys

release_path, series = sys.argv[1], sys.argv[2]
with open(release_path) as f:
    data = json.load(f)

pattern = re.compile(
    rf"^cpython-{re.escape(series)}\.\d+\+\d+-aarch64-apple-darwin-install_only\.tar\.gz$"
)

candidates = [a for a in data.get("assets", []) if pattern.match(a["name"])]
if not candidates:
    sys.exit(f"No matching asset found for series {series}")

# Just in case more than one patch version is present, pick the lexicographically
# highest name (patch versions sort correctly for single/double digit releases
# in practice; there is normally exactly one match per release anyway).
candidates.sort(key=lambda a: a["name"])
print(candidates[-1]["browser_download_url"])
PYEOF
)"

ASSET_NAME="$(basename "$ASSET_URL")"
TARBALL_PATH="$CACHE_DIR/$ASSET_NAME"

echo "[build_python_runtime] Selected asset: $ASSET_NAME"

if [[ ! -f "$TARBALL_PATH" ]]; then
    echo "[build_python_runtime] Downloading $ASSET_URL"
    curl -fL "$ASSET_URL" -o "$TARBALL_PATH.part"
    mv "$TARBALL_PATH.part" "$TARBALL_PATH"
else
    echo "[build_python_runtime] Using cached download at $TARBALL_PATH"
fi

# ---------------------------------------------------------------------------
# 3. Extract into build/python-runtime.
# ---------------------------------------------------------------------------
echo "[build_python_runtime] Extracting runtime..."
rm -rf "$RUNTIME_DIR"
mkdir -p "$RUNTIME_DIR"
# The tarball contains a top-level "python/" directory; strip it so that
# python-runtime/bin, python-runtime/lib etc. exist directly.
tar -xzf "$TARBALL_PATH" -C "$RUNTIME_DIR" --strip-components=1

PYTHON_BIN="$RUNTIME_DIR/bin/python3"
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "[build_python_runtime] ERROR: expected python3 at $PYTHON_BIN after extraction" >&2
    exit 1
fi

echo "[build_python_runtime] Runtime python: $("$PYTHON_BIN" --version)"

# ---------------------------------------------------------------------------
# 4. Use the runtime's own pip to install the vendored markitdown package.
# ---------------------------------------------------------------------------
# Every package is pinned to the version recorded in runtime-constraints.txt
# (see that file for how to upgrade deliberately). Without it each rebuild
# pulled the newest docling and dependencies, i.e. an engine nobody had tested.
CONSTRAINTS_FILE="$SCRIPT_DIR/runtime-constraints.txt"
if [[ ! -f "$CONSTRAINTS_FILE" ]]; then
    echo "[build_python_runtime] ERROR: missing $CONSTRAINTS_FILE" >&2
    exit 1
fi

echo "[build_python_runtime] Upgrading pip..."
"$PYTHON_BIN" -m pip install --upgrade pip --quiet

echo "[build_python_runtime] Installing engine/vendor/markitdown[$EXTRAS] ..."
"$PYTHON_BIN" -m pip install --quiet -c "$CONSTRAINTS_FILE" "$VENDOR_PKG[$EXTRAS]"

echo "[build_python_runtime] Verifying import..."
"$PYTHON_BIN" -c "from markitdown import MarkItDown; print('markitdown OK:', MarkItDown)"

# ---------------------------------------------------------------------------
# 5. Install docling (ML-based layout engine used for non-Hebrew PDFs --
#    see engine/convert_driver.py) and pre-download its model weights so
#    the app never needs network access at runtime.
#
#    docling isn't one of engine/vendor/markitdown's own extras -- it's a
#    separate engine this app's driver routes to conditionally -- so it's
#    installed as its own step rather than folded into $EXTRAS above.
#    This substantially increases install time/size (PyTorch + docling's
#    own dependencies, then ~500MB+ of model weights): expected and fine.
# ---------------------------------------------------------------------------
echo "[build_python_runtime] Installing docling..."
"$PYTHON_BIN" -m pip install --quiet -c "$CONSTRAINTS_FILE" docling

echo "[build_python_runtime] Verifying docling import..."
"$PYTHON_BIN" -c "from docling.document_converter import DocumentConverter; print('docling OK:', DocumentConverter)"

echo "[build_python_runtime] Pre-downloading docling model weights (this can take a few minutes)..."
HF_CACHE_DIR="$BUILD_DIR/hf-cache"
mkdir -p "$HF_CACHE_DIR"
WARMUP_PDF="$CACHE_DIR/docling-warmup.pdf"

# A minimal hand-built one-page PDF (no external PDF-generation library is
# installed in the runtime at this point) -- just enough real text content
# to force docling's full layout + text pipeline (and therefore all of its
# model downloads) to run once, against the redirected HF_HOME below.
python3 - "$WARMUP_PDF" <<'PYEOF'
import sys

out_path = sys.argv[1]
content = b"BT /F1 24 Tf 72 712 Td (Docling model warm-up) Tj ET"

objs = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
    b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
]

buf = bytearray(b"%PDF-1.4\n")
offsets = [0]
for i, obj in enumerate(objs, start=1):
    offsets.append(len(buf))
    buf += ("%d 0 obj\n" % i).encode()
    buf += obj
    buf += b"\nendobj\n"

xref_offset = len(buf)
n = len(objs) + 1
buf += ("xref\n0 %d\n" % n).encode()
buf += b"0000000000 65535 f \n"
for off in offsets[1:]:
    buf += ("%010d 00000 n \n" % off).encode()
buf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (n, xref_offset)

with open(out_path, "wb") as f:
    f.write(bytes(buf))
PYEOF

# HF_HOME redirected to the bundle-friendly cache dir so the model download
# below lands where build_app.sh will pick it up. Deliberately does NOT set
# HF_HUB_OFFLINE here -- this is the one time network access is wanted, to
# populate the cache. rapidocr's own models download separately, straight
# into its installed package directory under site-packages/rapidocr/models
# (not HF_HOME) -- since the whole python-runtime tree is copied wholesale
# into the app bundle, that lands in the bundle automatically too.
if HF_HOME="$HF_CACHE_DIR" "$PYTHON_BIN" -c "
from docling.document_converter import DocumentConverter
conv = DocumentConverter()
result = conv.convert('$WARMUP_PDF')
text = result.document.export_to_markdown()
assert 'warm-up' in text.lower(), f'unexpected warm-up output: {text!r}'
print('docling warm-up OK:', text.strip())
"; then
    echo "[build_python_runtime] Model warm-up complete. HF cache: $HF_CACHE_DIR"
else
    echo "[build_python_runtime] ERROR: docling model warm-up failed -- the app" >&2
    echo "  would otherwise try to download models over the network on first" >&2
    echo "  launch, breaking the offline guarantee. Check network access and" >&2
    echo "  retry (FORCE_REBUILD=1 to force a full rebuild)." >&2
    exit 1
fi

RAPIDOCR_MODEL_COUNT="$(find "$RUNTIME_DIR"/lib/python*/site-packages/rapidocr/models -name '*.onnx' 2>/dev/null | wc -l | tr -d ' ')"
if [[ "$RAPIDOCR_MODEL_COUNT" -eq 0 ]]; then
    echo "[build_python_runtime] ERROR: expected rapidocr .onnx models under" >&2
    echo "  site-packages/rapidocr/models after warm-up, found none." >&2
    exit 1
fi
echo "[build_python_runtime] rapidocr models present ($RAPIDOCR_MODEL_COUNT .onnx files) -- bundled automatically via python-runtime/."

echo "$VENDOR_STAMP" > "$STAMP_FILE"
echo "[build_python_runtime] Done. Runtime ready at $RUNTIME_DIR"
