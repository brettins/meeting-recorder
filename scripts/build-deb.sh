#!/usr/bin/env bash
# Builds the Linux .deb, taking the version from git rather than from whoever
# is running it. Mirrors the "Build .deb" step in .github/workflows/release.yml.
#
#   git tag v1.2.2+brett1 && scripts/build-deb.sh
#
# An untagged or dirty tree still builds, but the version says so, so an
# in-progress build can never be mistaken for a release.
set -euo pipefail

cd "$(dirname "$0")/.."

if ! git describe --tags >/dev/null 2>&1; then
    echo "error: no tags found — tag a commit first, e.g. git tag v1.0.0+brett1" >&2
    exit 1
fi

# Debian versions use '-' to separate the debian revision, so git describe's
# "-<n>-g<sha>" suffix has to become '+' or dpkg reads it as a revision.
RAW=$(git describe --tags --dirty=-dirty)
VERSION=${RAW#v}
VERSION=${VERSION//-/+}

COMMIT=$(git rev-parse --short HEAD)
echo "version : $VERSION"
echo "commit  : $COMMIT"
git diff --quiet || echo "WARNING: working tree is dirty — this is not a reproducible build"

STAGING=$(mktemp -d)
trap 'rm -rf "$STAGING"' EXIT
mkdir -p "$STAGING/DEBIAN" "$STAGING/opt/meeting-recorder/linux/src" \
         "$STAGING/usr/bin" "$STAGING/usr/share/applications"

cp -r linux/src/. "$STAGING/opt/meeting-recorder/linux/src/"
cp linux/requirements.txt linux/requirements.lock "$STAGING/opt/meeting-recorder/"
cp linux/packaging/usr/bin/meeting-recorder "$STAGING/usr/bin/meeting-recorder"
chmod 755 "$STAGING/usr/bin/meeting-recorder"
cp linux/packaging/usr/share/applications/io.github.dipakmdhrm.MeetingRecorder.desktop \
   "$STAGING/usr/share/applications/"

ICONS_SRC="linux/src/meeting_recorder/assets/icons/hicolor"
for size in 16 24 32 48 64 128 256; do
    dest="$STAGING/usr/share/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$dest"
    cp "$ICONS_SRC/${size}x${size}/apps/meeting-recorder.png" "$dest/meeting-recorder.png"
done
mkdir -p "$STAGING/usr/share/icons/hicolor/scalable/apps"
cp "$ICONS_SRC/scalable/apps/meeting-recorder.svg" \
   "$STAGING/usr/share/icons/hicolor/scalable/apps/meeting-recorder.svg"

for f in postinst prerm postrm; do
    cp "linux/packaging/DEBIAN/$f" "$STAGING/DEBIAN/$f"
    chmod 755 "$STAGING/DEBIAN/$f"
done
sed "s/@VERSION@/${VERSION}/" linux/packaging/DEBIAN/control.template > "$STAGING/DEBIAN/control"

OUT="${1:-$HOME/Downloads}/meeting-recorder_${VERSION}_all.deb"
dpkg-deb --build "$STAGING" "$OUT" >/dev/null
echo "built   : $OUT"
echo
echo "install with:  sudo dpkg -i $OUT"
