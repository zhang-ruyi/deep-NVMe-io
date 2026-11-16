# Reproduction

## Requirements

- Linux with tracefs (`/sys/kernel/tracing`) and root access.
- `fio` (tested 3.36), `bpftrace` (tested 0.20.2), `nvme-cli`, `lspci`, `python3`.
- A directory on the NVMe namespace under test (`WORK`). The scripts only create/read a **regular file**; they never write a raw block device.
- Enough free space for the test file. Default 4K capture uses 512MiB; matrix uses 16MiB per phase plus raw trace files.
- Optional: `drgn` plus matching vmlinux/module DWARF debug info for `analysis/drgn_nvme.py`. See [drgn.md](drgn.md).

Kernel and device used for the published data:

```
Linux 7.0.0-43-generic
Silicon Motion PCIe-8 SSD 512GB, PCIe 3.0 x4
max_sectors_kb=128, max_segments=33, logical_block_size=512
NVMe MDTS=6 (256KiB), IOMMU on
```

## Quick start: block-size matrix

```bash
git clone git@github.com:zhang-ruyi/deep-NVMe-io.git
cd deep-NVMe-io

LAB=/path/on/nvme/nvme-lab
mkdir -p "$LAB"

sudo WORK="$LAB" OUT="$LAB/matrix" SIZE=16M \
  ./scripts/bs_matrix.sh
```

Optional environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `WORK` | `$PWD` | directory for the fio test file |
| `OUT` | `$WORK/nvme_bs_matrix` | capture output directory |
| `FILE` | `$WORK/fio-bs-matrix.bin` | test file |
| `SIZE` | `16M` | fio `--size` per phase |
| `BS_LIST` | `512 1k 2k 4k 8k 16k 32k 64k 128k 256k 512k 1m` | fio block sizes |
| `NVME_DEV` | `/dev/nvme0` | controller char device |
| `NVME_NS` | `/dev/nvme0n1` | namespace block device |
| `PCI_BDF` | `04:00.0` | PCI address for `lspci` |
| `TR` | `/sys/kernel/tracing` | tracefs mount |
| `BPF` | `scripts/bs_matrix.bt` | bpftrace program |

The script must run as root. It uses an interactive `sudo` password prompt; run it from a terminal.

### Outputs

```
$OUT/
  manifest.txt                 # bs, op, monotonic start/end, fio rc
  trace_<bs>_<op>.txt          # block + NVMe trace per phase
  trace_admin.txt              # qid 0 Identify / Get Log Page / Get Features
  nvme_matrix_bpf.log          # batch lengths + sampled SQE fields
  bpftrace.err                 # should be empty
  fio_<bs>_<op>.json           # fio JSON
  meta.txt, queue_limits.txt, nvme_id_ctrl.txt, nvme_id_ns.txt, lspci_nvme.txt
```

## Quick start: single 4K capture with full 64-byte SQE

```bash
LAB=/path/on/nvme/nvme-lab
sudo WORK="$LAB" OUT="$LAB/capture" FIO_SIZE=512M \
  ./scripts/nvme_capture.sh
```

Outputs include `trace.txt`, `nvme_sqe.log`, `fio_write.json`, `fio_read.json`, and device metadata. `nvme_sqe.log` contains sampled full SQEs (default cap: 256 writes + 256 reads).

## Analysis

### Matrix capture

```bash
python3 analysis/analyze_matrix.py --capture "$LAB/matrix" --out "$LAB/matrix/summary"
python3 analysis/make_charts.py --data "$LAB/matrix/summary" --out "$LAB/matrix/charts"
```

The first command writes:

- `matrix_summary.csv` / `.json`
- `batch_summary.csv`
- `sqe_samples.csv`
- `admin_summary.csv`

The second command writes six SVGs. Open them in a browser or embed them in Markdown.

### Full 64-byte SQE log

```bash
python3 analysis/decode_sqe.py --log "$LAB/capture/nvme_sqe.log" \
  --out "$LAB/capture/sqe_full_4k.csv"
```

## How to read the outputs

1. **Check trace completeness.** In each `trace_*.txt` header, compare `entries-in-buffer` with `entries-written`; they should match. Search for `OVERRUN`.
2. **Compare block requests with NVMe commands.** Use `block_rq_issue` (not fio IOs) against `nvme_setup_cmd` with `qid>=1`.
3. **Compare bytes.** Sum request bytes and NVMe `(NLB+1)*512`; they should match for read/write data.
4. **Check LBA mapping.** For a 1:1 command, `block sector + partition start sector == NVMe SLBA`.
5. **Separate qid 0.** Admin commands have no block bio; do not include them in data IO counts.
6. **Watch for filesystem effects.** The 512B write in this dataset produces extra 3,584B writes at both block and NVMe layers. Confirm on another filesystem or a raw namespace before treating it as generic NVMe behavior.
7. **Split vs merge.** For >128KiB fio IOs, block requests/NVMe commands exceed fio IO count; for the 512B anomaly, fio IO count is lower than block/NVMe. Merges would make block requests fewer than bios.

## Troubleshooting

- `drgn could not open the live kernel: Permission denied: '/proc/kcore'`: run drgn as root.
- `missing type struct nvme_dev`: install matching `nvme`/`nvme_core` module debug info, not just vmlinux; drgn 0.3.0 does not use BTF.
- `bpftrace currently only supports running as the root user`: run the script with `sudo`.
- `bpftrace` exits before fio: check `bpftrace.err`; verify the kernel symbols in `scripts/*.bt` exist in `available_filter_functions`.
- `missing tracepoint ...`: check `/sys/kernel/tracing/events/nvme` and `events/block`; tracefs may not be mounted or permissions may block access.
- No `nvme_submit_cmds.part.0`: kernel compiler split names differ; use `grep nvme_submit_cmds /sys/kernel/tracing/available_filter_functions` and adjust the probe.
- `trace_clock` restore warning: harmless; the script prefers `mono` for correlation with bpftrace `nsecs` and restores the previous clock when possible.
- Trace lost events: increase `buffer_size_kb` in the script or reduce `SIZE`/`BS_LIST`.

## Safety

- Never point `FILE` at a raw device or a mounted filesystem metadata area.
- Use a scratch directory on the namespace under test.
- Remove test files after the run.
- The scripts change global tracefs settings temporarily; they restore the clock and buffer size and disable their events.
