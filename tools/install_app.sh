#!/usr/bin/env bash
#
# Build (optionally) and install MarkItDown.app into /Applications, safely.
#
#   ./tools/install_app.sh              build, then install (or update) the app
#   ./tools/install_app.sh --no-build   install the existing build/MarkItDown.app
#
# Environment overrides:
#   INSTALL_DIR   where to install (default /Applications; use ~/Applications
#                 if you cannot write to /Applications)
#   APP_SOURCE    app bundle to install (default build/MarkItDown.app; used by tests)
#
# Why this is a separate script from build_app.sh: build_app.sh deletes
# build/MarkItDown.app before it re-creates it, so a failed build leaves no
# working app there. The installed copy must survive that. This script therefore
#   1. verifies the freshly built bundle is complete and correctly signed,
#   2. copies it next to the destination under a temporary name,
#   3. verifies the copy,
#   4. swaps it into place, keeping the previous version until the swap is
#      verified, and rolls back if it is not.
# At no point is the installed app removed before a verified replacement exists.
#
# macOS bash 3.2 compatible.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

APP_NAME="MarkItDown"
EXECUTABLE_NAME="MarkItDownMac"
BUNDLE_ID="com.local.markitdown"

SRC="${APP_SOURCE:-$PROJECT_ROOT/build/$APP_NAME.app}"
INSTALL_DIR="${INSTALL_DIR:-/Applications}"
DEST="$INSTALL_DIR/$APP_NAME.app"
# Hidden, same-volume siblings of DEST, so that the final mv is an atomic rename.
STAGE="$INSTALL_DIR/.$APP_NAME.app.installing"
PREVIOUS="$INSTALL_DIR/.$APP_NAME.app.previous"

DO_BUILD=1
for arg in "$@"; do
    case "$arg" in
        --no-build) DO_BUILD=0 ;;
        -h|--help)
            sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown option: $arg (use --no-build or --help)" >&2
            exit 2
            ;;
    esac
done

fail() { echo "ERROR: $*" >&2; exit 1; }

# The paths below are removed with rm -rf, so refuse to run if any is empty or
# does not have the expected shape.
for p in "$DEST" "$STAGE" "$PREVIOUS"; do
    case "$p" in
        /?*/*MarkItDown.app*) ;;
        *) fail "refusing to continue: unexpected install path '$p'" ;;
    esac
done

# Verify a bundle is complete, signed and able to import the converters.
# Catches the failure mode seen on 2026-10-09 where a half-copied runtime
# (missing stdlib and site-packages files) still looked like an .app.
verify_bundle() {
    local app="$1" label="$2" py
    py="$app/Contents/Resources/python-runtime/bin/python3"
    [[ -x "$app/Contents/MacOS/$EXECUTABLE_NAME" ]] \
        || fail "$label: missing executable Contents/MacOS/$EXECUTABLE_NAME"
    [[ -f "$app/Contents/Resources/convert_driver.py" ]] \
        || fail "$label: missing Contents/Resources/convert_driver.py"
    [[ -x "$py" ]] || fail "$label: missing bundled Python (python-runtime/bin/python3)"
    [[ -d "$app/Contents/Resources/hf-cache" ]] \
        || fail "$label: missing Contents/Resources/hf-cache (offline Docling models)"
    # -B: do not write bytecode. Importing a module that has no cached .pyc would
    # otherwise create __pycache__ files inside the sealed bundle and invalidate
    # its code signature. (-I alone is not enough: isolated mode ignores
    # PYTHONDONTWRITEBYTECODE. This is what broke the first real install.)
    "$py" -I -B -c "import markitdown, pdfminer, pdfplumber, bidi, docx, pptx, openpyxl, lxml, mammoth" \
        >/dev/null 2>&1 \
        || fail "$label: bundled Python cannot import the converters (incomplete runtime?)"
    # Signature check LAST, so that anything the checks above changed is caught.
    codesign --verify --deep --strict "$app" >/dev/null 2>&1 \
        || fail "$label: code signature is missing or invalid"
}

if [[ "$DO_BUILD" == "1" ]]; then
    echo "== 1/5: build =="
    "$SCRIPT_DIR/build_app.sh"
else
    echo "== 1/5: build skipped (--no-build) =="
fi

echo "== 2/5: verify source bundle =="
[[ -d "$SRC" ]] || fail "$SRC does not exist; run without --no-build"
verify_bundle "$SRC" "source"
echo "source bundle OK: $SRC"

[[ -d "$INSTALL_DIR" ]] || fail "$INSTALL_DIR does not exist"
[[ -w "$INSTALL_DIR" ]] \
    || fail "cannot write to $INSTALL_DIR; re-run with INSTALL_DIR=\$HOME/Applications"

echo "== 3/5: close the running app (if any) =="
is_running() { pgrep -x "$EXECUTABLE_NAME" >/dev/null 2>&1; }
if is_running; then
    osascript -e "tell application id \"$BUNDLE_ID\" to quit" >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        is_running || break
        sleep 1
    done
    if is_running; then
        fail "$APP_NAME is still running after 10 s; quit it (it may be mid-conversion) and re-run"
    fi
    echo "app closed"
else
    echo "app not running"
fi

echo "== 4/5: copy to a staging path and verify =="
# A failed or interrupted copy must never leave a half-built bundle behind.
# (After a successful swap STAGE no longer exists, so this trap is then a no-op.)
trap 'rm -rf "$STAGE"' EXIT
rm -rf "$STAGE"
# rsync rather than ditto: it can leave Finder's .DS_Store files out, which are
# not part of the app. -a keeps symlinks, permissions and timestamps; the
# codesign check below proves the copy is still a valid, sealed bundle.
rsync -a --exclude .DS_Store "$SRC/" "$STAGE/" \
    || fail "copy to $STAGE failed; the installed app was not touched"
if ! ( verify_bundle "$STAGE" "staged copy" ); then
    fail "staged copy failed verification; the installed app was not touched"
fi

echo "== 5/5: swap into place =="
rm -rf "$PREVIOUS"
if [[ -e "$DEST" || -L "$DEST" ]]; then
    mv "$DEST" "$PREVIOUS" || fail "could not move the installed app aside; nothing was changed"
fi
if ! mv "$STAGE" "$DEST"; then
    # DEST is missing right now: put the previous version back before failing.
    if [[ -e "$PREVIOUS" || -L "$PREVIOUS" ]]; then
        mv "$PREVIOUS" "$DEST" && echo "Previous version restored." >&2
    fi
    fail "could not move the new app into place"
fi
if ! ( verify_bundle "$DEST" "installed app" ); then
    echo "Verification of the installed app failed; rolling back." >&2
    rm -rf "$DEST"
    if [[ -e "$PREVIOUS" || -L "$PREVIOUS" ]]; then
        mv "$PREVIOUS" "$DEST"
        echo "Previous version restored." >&2
    fi
    exit 1
fi
rm -rf "$PREVIOUS"

# Strip a download quarantine flag if one was somehow inherited (harmless if absent).
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true

echo
echo "Installed: $DEST"
if command -v git >/dev/null 2>&1 && git -C "$PROJECT_ROOT" rev-parse --short HEAD >/dev/null 2>&1; then
    echo "Project HEAD at install time: $(git -C "$PROJECT_ROOT" rev-parse --short HEAD)"
fi
echo "Open it with:  open \"$DEST\""
