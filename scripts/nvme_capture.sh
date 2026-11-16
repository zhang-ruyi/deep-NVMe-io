#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WORK=${WORK:-$PWD}
OUT=${OUT:-$WORK/nvme_capture}
TESTFILE=${TESTFILE:-$WORK/fio-test.bin}
TR=${TR:-/sys/kernel/tracing}
BPF=${BPF:-$SCRIPT_DIR/sqe.bt}
FIO_SIZE=${FIO_SIZE:-512M}
NVME_DEV=${NVME_DEV:-/dev/nvme0}
NVME_NS=${NVME_NS:-/dev/nvme0n1}
PCI_BDF=${PCI_BDF:-04:00.0}
OWNER_UID=${SUDO_UID:-}
OWNER_GID=${SUDO_GID:-}
BPF_PID=
STARTED=0
DONE=0
OLD_CLOCK=
OLD_BUFSZ=

if [[ $(id -u) -ne 0 ]]; then
  echo "run with sudo: sudo $0" >&2
  exit 1
fi

mkdir -p "$OUT"
if [[ -n $OWNER_UID ]]; then
  chown "$OWNER_UID:$OWNER_GID" "$OUT" 2>/dev/null || true
fi

cleanup() {
  local rc=$?
  set +e
  if [[ $STARTED -eq 1 ]]; then
    echo 0 > "$TR/tracing_on" 2>/dev/null
  fi
  if [[ -n ${BPF_PID:-} ]] && kill -0 "$BPF_PID" 2>/dev/null; then
    kill -INT "$BPF_PID" 2>/dev/null
    wait "$BPF_PID" 2>/dev/null
  fi
  if [[ $DONE -eq 0 && -f "$TR/trace" ]]; then
    cp "$TR/trace" "$OUT/trace_partial.txt" 2>/dev/null
  fi
  for e in nvme/nvme_setup_cmd nvme/nvme_complete_rq block/block_bio_queue block/block_rq_issue; do
    [[ -w "$TR/events/$e/enable" ]] && echo 0 > "$TR/events/$e/enable" 2>/dev/null
  done
  [[ -n ${OLD_CLOCK:-} ]] && echo "$OLD_CLOCK" > "$TR/trace_clock" 2>/dev/null
  [[ -n ${OLD_BUFSZ:-} ]] && echo "$OLD_BUFSZ" > "$TR/buffer_size_kb" 2>/dev/null
  if [[ -n $OWNER_UID ]]; then
    chown -R "$OWNER_UID:$OWNER_GID" "$OUT" 2>/dev/null
    chown "$OWNER_UID:$OWNER_GID" "$TESTFILE" 2>/dev/null
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
cat /proc/interrupts > "$OUT/interrupts.txt" 2>&1 || true
dmesg | grep -iE 'nvme|iommu' > "$OUT/dmesg_nvme_iommu.txt" 2>&1 || true

avail=$(df -Pk "$WORK" | awk 'NR==2 {print $4}')
if [[ ${avail:-0} -lt 1048576 ]]; then
  echo "not enough free space in $WORK" >&2
  exit 1
fi

mountpoint -q "$TR" || mount -t tracefs tracefs "$TR"

OLD_CLOCK=$(cat "$TR/trace_clock")
OLD_BUFSZ=$(cat "$TR/buffer_size_kb")
echo 0 > "$TR/tracing_on"
: > "$TR/trace"
echo 32768 > "$TR/buffer_size_kb"
if ! echo mono > "$TR/trace_clock" 2>> "$OUT/trace_setup.txt"; then
  echo "trace_clock mono unavailable" >> "$OUT/trace_setup.txt"
fi
for e in nvme/nvme_setup_cmd nvme/nvme_complete_rq block/block_bio_queue block/block_rq_issue; do
  if [[ -w "$TR/events/$e/enable" ]]; then
    echo 1 > "$TR/events/$e/enable"
  else
    echo "missing tracepoint $e" >> "$OUT/trace_setup.txt"
  fi
done

if bpftrace -d "$BPF" > "$OUT/bpftrace_dryrun.log" 2>&1; then
  bpftrace -B none -o "$OUT/nvme_sqe.log" "$BPF" 2> "$OUT/bpftrace.err" &
  BPF_PID=$!
  for i in $(seq 1 60); do
    grep -q '^Attaching' "$OUT/nvme_sqe.log" 2>/dev/null && break
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

STARTED=1
echo 1 > "$TR/tracing_on"

fio --name=nvme_write --filename="$TESTFILE" --size="$FIO_SIZE" --bs=4k --rw=write \
  --ioengine=libaio --iodepth=8 --numjobs=1 --direct=1 --buffer_pattern=0xdeadbeef \
  --group_reporting --eta=never --output-format=json --output="$OUT/fio_write.json"

fio --name=nvme_read --filename="$TESTFILE" --size="$FIO_SIZE" --bs=4k --rw=read \
  --ioengine=libaio --iodepth=8 --numjobs=1 --direct=1 \
  --group_reporting --eta=never --output-format=json --output="$OUT/fio_read.json"

echo 0 > "$TR/tracing_on"
STARTED=0

if [[ -n ${BPF_PID:-} ]] && kill -0 "$BPF_PID" 2>/dev/null; then
  kill -INT "$BPF_PID"
  wait "$BPF_PID" || true
  BPF_PID=
fi

cp "$TR/trace" "$OUT/trace.txt"
DONE=1

for e in nvme/nvme_setup_cmd nvme/nvme_complete_rq block/block_bio_queue block/block_rq_issue; do
  [[ -w "$TR/events/$e/enable" ]] && echo 0 > "$TR/events/$e/enable"
done
echo "$OLD_CLOCK" > "$TR/trace_clock" 2>> "$OUT/trace_setup.txt" || true
echo "$OLD_BUFSZ" > "$TR/buffer_size_kb" 2>> "$OUT/trace_setup.txt" || true

if [[ -n $OWNER_UID ]]; then
  chown -R "$OWNER_UID:$OWNER_GID" "$OUT"
  chown "$OWNER_UID:$OWNER_GID" "$TESTFILE" 2>/dev/null || true
fi
echo "capture done: $OUT"
