#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
REL=$(uname -r)
SRC=/usr/lib/debug/lib/modules/$REL
CACHE=${DRGN_NVME_DEBUG_CACHE:-/tmp/drgn-nvme-debug-$REL}
DRGN=${DRGN:-}

if [[ $(id -u) -ne 0 ]]; then
  echo "run with sudo: sudo $0" >&2
  exit 1
fi

if [[ -z ${DRGN:-} ]]; then
  if [[ -x "$ROOT/.venv/bin/drgn" ]]; then
    DRGN="$ROOT/.venv/bin/drgn"
  else
    DRGN=$(command -v drgn || true)
  fi
fi
if [[ -z ${DRGN:-} ]]; then
  echo "drgn not found; install it or set DRGN=/path/to/drgn" >&2
  exit 1
fi
if ! command -v zstd >/dev/null 2>&1; then
  echo "zstd not found; install zstd to decompress Ubuntu module debug files" >&2
  exit 1
fi
if [[ ! -d "$SRC/kernel/drivers/nvme" ]]; then
  echo "no NVMe module debug files under $SRC" >&2
  echo "install linux-image-unsigned-$REL-dbgsym (and matching dbgsym packages)" >&2
  exit 1
fi

mkdir -p "$CACHE"
while IFS= read -r -d '' f; do
  rel=${f#"$SRC/"}
  out="$CACHE/lib/modules/$REL/${rel%.zst}"
  mkdir -p "$(dirname "$out")"
  if [[ ! -s "$out" || "$f" -nt "$out" ]]; then
    zstd -d -f -q -c "$f" > "$out"
  fi
done < <(find "$SRC/kernel/drivers/nvme" -name '*.ko.zst' -print0)

chmod -R a+rX "$CACHE"
exec "$DRGN" --debug-directory "$CACHE" "$ROOT/analysis/drgn_nvme.py" "$@"
