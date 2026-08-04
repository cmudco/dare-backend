#!/usr/bin/env bash
# Vendors the shared cmu-brand package and wordmark asset into every quill.
# Quills must be self-contained (quillmark loads each quill dir in isolation),
# so shared sources are copied, never symlinked. Re-run after editing shared/.
set -euo pipefail
cd "$(dirname "$0")/.."

for quill_dir in quills/*/*/; do
  rm -rf "${quill_dir}packages/cmu-brand"
  mkdir -p "${quill_dir}packages" "${quill_dir}assets"
  cp -R shared/cmu-brand "${quill_dir}packages/cmu-brand"
  cp shared/assets/cmu-wordmark.svg "${quill_dir}assets/"
  echo "synced ${quill_dir}"
done
