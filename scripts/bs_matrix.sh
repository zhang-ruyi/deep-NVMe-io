#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WORK=${WORK:-$PWD}
OUT=${OUT:-$WORK/nvme_bs_matrix}
FILE=${FILE:-$WORK/fio-bs-matrix.bin}
TR=${TR:-/sys/kernel/tracing}
BPF=${BPF:-$SCRIPT_DIR/bs_matrix.bt}
SIZE=${SIZE:-16M}
BS_LIST=${BS_LIST:-"512 1k 2k 4k 8k 16k 32k 64k 128k 256k 512k 1m"}
NVME_DEV=${NVME_DEV:-/dev/nvme0}
NVME_NS=${NVME_NS:-/dev/nvme0n1}
PCI_BDF=${PCI_BDF:-04:00.0}
OWNER_UID=${SUDO_UID:-}
OWNER_GID=${SUDO_GID:-}
BPF_PID=
OLD_CLOCK=
OLD_BUFSZ=
DONE=0

if [[ $(id -u) -ne 0 ]]; then
  echo "run with sudo: sudo $0" >&2
  exit 1
fi

mkdir -p "$OUT"
if [[ -n $OWNER_UID ]]; then
  chown "$OWNER_UID:$OWNER_GID" "$OUT" 2>/dev/null || true
fi
rm -f "$FILE"
fallocate -l "$SIZE" "$FILE" 2>/dev/null || truncate -s "$SIZE" "$FILE"

mono() { python3 -c 'import time; print(time.monotonic_ns())'; }

cleanup() {
  local rc=$?
  set +e
  echo 0 > "$TR/tracing_on" 2>/dev/null
  if [[ -n ${BPF_PID:-} ]] && kill -0 "$BPF_PID" 2>/dev/null; then
    kill -INT "$BPF_PID" 2>/dev/null
    wait "$BPF_PID" 2>/dev/null
  fi
  if [[ $DONE -eq 0 ]]; then
    cp "$TR/trace" "$OUT/trace_partial.txt" 2>/dev/null
  fi
  for e in nvme/nvme_setup_cmd nvme/nvme_complete_rq block/block_bio_queue block/block_rq_issue; do
    [[ -w "$TR/events/$e/enable" ]] && echo 0 > "$TR/events/$e/enable" 2>/dev/null
  done
  [[ -n ${OLD_CLOCK:-} ]] && echo "$OLD_CLOCK" > "$TR/trace_clock" 2>/dev/null
  [[ -n ${OLD_BUFSZ:-} ]] && echo "$OLD_BUFSZ" > "$TR/buffer_size_kb" 2>/dev/null
  if [[ -n $OWNER_UID ]]; then
    chown -R "$OWNER_UID:$OWNER_GID" "$OUT" 2>/dev/null
    chown "$OWNER_UID:$OWNER_GID" "$FILE" 2>/dev/null
  fi
  exit "$rc"
}
trap cleanup EXIT

{
  date -Is
  uname -a
  fio --version
  cat /proc/cmdline
  echo "iommu_groups=$(ls /sys/kernel/iommu_groups 2>/dev/null | wc -l)"
} > "$OUT/meta.txt" 2>&1

nvme id-ctrl "$NVME_DEV" > "$OUT/nvme_id_ctrl.txt" 2>&1 || true
nvme id-ns "$NVME_NS" > "$OUT/nvme_id_ns.txt" 2>&1 || true
lspci -vv -s "$PCI_BDF" > "$OUT/lspci_nvme.txt" 2>&1 || true
for f in max_sectors_kb max_hw_sectors_kb max_segments max_segment_size logical_block_size physical_block_size nr_requests scheduler nomerges rotational; do
  echo "$f=$(cat /sys/block/$(basename "$NVME_NS")/queue/$f 2>/dev/null)" >> "$OUT/queue_limits.txt"
done

mountpoint -q "$TR" || mount -t tracefs tracefs "$TR"
OLD_CLOCK=$(cat "$TR/trace_clock")
OLD_BUFSZ=$(cat "$TR/buffer_size_kb")
echo 0 > "$TR/tracing_on"
: > "$TR/trace"
echo 32768 > "$TR/buffer_size_kb"
echo mono > "$TR/trace_clock" 2>> "$OUT/trace_setup.txt" || true
for e in nvme/nvme_setup_cmd nvme/nvme_complete_rq block/block_bio_queue block/block_rq_issue; do
  if [[ -w "$TR/events/$e/enable" ]]; then
    echo 1 > "$TR/events/$e/enable"
  else
    echo "missing tracepoint $e" >> "$OUT/trace_setup.txt"
  fi
done

: > "$TR/trace"
echo 1 > "$TR/tracing_on"
nvme id-ctrl "$NVME_DEV" > /dev/null 2>&1 || true
nvme smart-log "$NVME_DEV" > /dev/null 2>&1 || true
nvme get-feature -f 0x07 "$NVME_DEV" > /dev/null 2>&1 || true
echo 0 > "$TR/tracing_on"
cp "$TR/trace" "$OUT/trace_admin.txt"

if bpftrace -d "$BPF" > "$OUT/bpftrace_dryrun.log" 2>&1; then
  bpftrace -B none -o "$OUT/nvme_matrix_bpf.log" "$BPF" 2> "$OUT/bpftrace.err" &
  BPF_PID=$!
  for i in $(seq 1 60); do
    grep -q '^Attaching' "$OUT/nvme_matrix_bpf.log" 2>/dev/null && break
    sleep 0.5
  done
  sleep 1
  if ! kill -0 "$BPF_PID" 2>/dev/null; then
    echo "bpftrace exited before fio" >> "$OUT/bpftrace.err"
    BPF_PID=
  fi
else
  echo "bpftrace dry run failed" > "$OUT/bpftrace.err"
fi

: > "$OUT/manifest.txt"
for bs in $BS_LIST; do
  for op in write read; do
    : > "$TR/trace"
    ts0=$(mono)
    echo 1 > "$TR/tracing_on"
    set +e
    fio --name="bs_${bs}_${op}" --filename="$FILE" --size="$SIZE" --bs="$bs" --rw="$op" \
      --ioengine=libaio --iodepth=8 --numjobs=1 --direct=1 --buffer_pattern=0xdeadbeef \
      --group_reporting --eta=never --output-format=json \
      --output="$OUT/fio_${bs}_${op}.json" > "$OUT/fio_${bs}_${op}.stdout" 2> "$OUT/fio_${bs}_${op}.stderr"
    rc=$?
    set -e
    echo 0 > "$TR/tracing_on"
    ts1=$(mono)
    cp "$TR/trace" "$OUT/trace_${bs}_${op}.txt"
    echo "$bs $op $ts0 $ts1 $rc" >> "$OUT/manifest.txt"
  done
done

echo 0 > "$TR/tracing_on"
if [[ -n ${BPF_PID:-} ]] && kill -0 "$BPF_PID" 2>/dev/null; then
  kill -INT "$BPF_PID"
  wait "$BPF_PID" || true
  BPF_PID=
fi
DONE=1

for e in nvme/nvme_setup_cmd nvme/nvme_complete_rq block/block_bio_queue block/block_rq_issue; do
  [[ -w "$TR/events/$e/enable" ]] && echo 0 > "$TR/events/$e/enable"
done
echo "$OLD_CLOCK" > "$TR/trace_clock" 2>> "$OUT/trace_setup.txt" || true
echo "$OLD_BUFSZ" > "$TR/buffer_size_kb" 2>> "$OUT/trace_setup.txt" || true

if [[ -n $OWNER_UID ]]; then
  chown -R "$OWNER_UID:$OWNER_GID" "$OUT"
  chown "$OWNER_UID:$OWNER_GID" "$FILE" 2>/dev/null || true
fi
echo "matrix done: $OUT"
