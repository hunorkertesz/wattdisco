#!/bin/sh
# Pack "Watt Disco.app" into a .dmg with an Applications shortcut to drag it onto and the install steps.
# Usage: packaging/make_dmg.sh "dist/Watt Disco.app" WattDisco-mac.dmg
set -eu
app="$1"
out="$2"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
cp -R "$app" "$stage/"
cp "$(dirname "$0")/How to install (Mac).txt" "$stage/"
ln -s /Applications "$stage/Applications"
rm -f "$out"
for try in 1 2 3; do
  hdiutil create -volname "Watt Disco" -srcfolder "$stage" -fs HFS+ -format UDZO -imagekey zlib-level=9 "$out" && exit 0
  sleep 10  # "Resource busy": the freshly built app is still being indexed
done
exit 1
