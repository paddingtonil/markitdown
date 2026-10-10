#!/usr/bin/env bash
# Builds the MarkItDownMac Swift executable, builds/reuses the bundled
# Python runtime, and assembles build/MarkItDown.app.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$PROJECT_ROOT/build"
APP_DIR="$BUILD_DIR/MarkItDown.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

APP_NAME="MarkItDown"
BUNDLE_ID="com.local.markitdown"
EXECUTABLE_NAME="MarkItDownMac"
SHORT_VERSION="0.2.0"

# Build identification, so the installed app can be matched to a build: the build
# number is the commit count (shown by Finder as "Version 0.2.0 (N)") and the
# short commit hash, with "-dirty" when tracked files have uncommitted changes,
# goes into the custom MarkItDownGitCommit key. Falls back safely outside git.
BUILD_NUMBER="$(git -C "$PROJECT_ROOT" rev-list --count HEAD 2>/dev/null || echo 0)"
GIT_COMMIT="$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
if [[ -n "$(git -C "$PROJECT_ROOT" status --porcelain --untracked-files=no 2>/dev/null)" ]]; then
    GIT_COMMIT="${GIT_COMMIT}-dirty"
fi

# The Swift package (Package.swift + Sources/) lives in app/, so SwiftPM must
# run from there; its .build/ cache is created there too.
echo "== 1/4: swift build -c release =="
cd "$PROJECT_ROOT/app"
swift build -c release

SWIFT_BIN_DIR="$(swift build -c release --show-bin-path)"
BUILT_EXECUTABLE="$SWIFT_BIN_DIR/$EXECUTABLE_NAME"
if [[ ! -x "$BUILT_EXECUTABLE" ]]; then
    echo "ERROR: expected built executable at $BUILT_EXECUTABLE" >&2
    exit 1
fi

# SPM resources (currently just the bundled Caprasimo font) land in a
# generated <target>_<module>.bundle next to the executable.
SWIFT_RESOURCE_BUNDLE="$SWIFT_BIN_DIR/${EXECUTABLE_NAME}_${EXECUTABLE_NAME}.bundle"
if [[ ! -d "$SWIFT_RESOURCE_BUNDLE" ]]; then
    echo "ERROR: expected SPM resource bundle at $SWIFT_RESOURCE_BUNDLE" >&2
    exit 1
fi

echo "== 2/4: build/reuse bundled Python runtime =="
"$SCRIPT_DIR/build_python_runtime.sh"

echo "== 3/4: assemble $APP_DIR =="
rm -rf "$APP_DIR"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

cp "$BUILT_EXECUTABLE" "$MACOS_DIR/$EXECUTABLE_NAME"

# Copy the python runtime (dereference symlinks so the bundle is self
# contained) and the driver script into Resources.
cp -RL "$BUILD_DIR/python-runtime" "$RESOURCES_DIR/python-runtime"
cp "$PROJECT_ROOT/engine/convert_driver.py" "$RESOURCES_DIR/convert_driver.py"

# Bundle the pre-downloaded docling/Hugging Face model cache (built by
# build_python_runtime.sh) so the app never needs network access to fetch
# models at runtime -- see convert_driver.py's _locate_model_cache_dir().
if [[ -d "$BUILD_DIR/hf-cache" ]]; then
    cp -RL "$BUILD_DIR/hf-cache" "$RESOURCES_DIR/hf-cache"
else
    echo "ERROR: expected model cache at $BUILD_DIR/hf-cache (produced by build_python_runtime.sh) but it's missing." >&2
    exit 1
fi

# Bundle SPM resources (the Caprasimo display font, app icon source)
# alongside the executable so Bundle.module resolves inside the
# packaged .app too.
cp -R "$SWIFT_RESOURCE_BUNDLE" "$RESOURCES_DIR/$(basename "$SWIFT_RESOURCE_BUNDLE")"

# The app icon must additionally sit directly at Contents/Resources/
# (not nested inside the SPM resource bundle above) for CFBundleIconFile
# to find it.
cp "$PROJECT_ROOT/app/Sources/MarkItDownMac/Resources/Icon/AppIcon.icns" "$RESOURCES_DIR/AppIcon.icns"

cat > "$CONTENTS_DIR/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>${APP_NAME}</string>
    <key>CFBundleDisplayName</key>
    <string>${APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>${BUNDLE_ID}</string>
    <key>CFBundleExecutable</key>
    <string>${EXECUTABLE_NAME}</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>${SHORT_VERSION}</string>
    <key>CFBundleVersion</key>
    <string>${BUILD_NUMBER}</string>
    <key>MarkItDownGitCommit</key>
    <string>${GIT_COMMIT}</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.productivity</string>
</dict>
</plist>
PLIST

echo "== 4/4: ad-hoc codesign =="
codesign --force --deep -s - "$APP_DIR"

echo "Done. Built: $APP_DIR"
