#!/usr/bin/env bash
# Rebuild docs/Module5_Reference.pdf from docs/module5-reference.html.
#
# The figures in that HTML are transcribed from a real run. After the ARR
# reference or the ticket extract changes, re-run the pipeline first and update
# the numbers before regenerating -- a stale PDF that looks authoritative is
# worse than no PDF.
set -euo pipefail
cd "$(dirname "$0")/.."

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[[ -x "$CHROME" ]] || { echo "Google Chrome not found at $CHROME"; exit 1; }

"$CHROME" --headless --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="docs/Module5_Reference.pdf" \
  "file://$PWD/docs/module5-reference.html" 2>/dev/null

if command -v pdfinfo >/dev/null; then
  echo "docs/Module5_Reference.pdf -- $(pdfinfo docs/Module5_Reference.pdf | awk '/^Pages/{print $2}') pages"
else
  echo "docs/Module5_Reference.pdf written"
fi
