#!/usr/bin/env bash
# Veröffentlicht web/public/data als einzigen Commit auf dem Branch "daten"
# (ersetzt den alten Stand – der Git-Verlauf wächst dadurch nicht).
# Danach den Workflow "Web-App veröffentlichen" starten (Actions → Run workflow)
# oder einen Commit auf main pushen.
set -euo pipefail
cd "$(dirname "$0")/.."
SRC=web/public/data
TMP=$(mktemp -d)
cp -r "$SRC"/. "$TMP"/
cd "$TMP"
git init -q -b daten
git add -A
git -c user.name="${GIT_AUTHOR_NAME:-Starkregen-Spiegel}" -c user.email="${GIT_AUTHOR_EMAIL:-daten@localhost}" \
  commit -q -m "Daten $(date +%F) ($(du -sh . | cut -f1))"
REMOTE=$(git -C "$OLDPWD" remote get-url origin)
git push -q -f "$REMOTE" daten:daten
echo "Daten veröffentlicht: $(du -sh . | cut -f1) → Branch daten"
rm -rf "$TMP"
