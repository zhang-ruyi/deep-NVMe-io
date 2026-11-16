# drgn: live NVMe queue / PRP / request inspection

`analysis/drgn_nvme.py` is a read-only drgn script for the **real kernel path**. It complements the tracepoints:

- tracefs/bpftrace: event stream, low overhead;
- drgn: snapshot of driver queues, in-flight requests, SQE bytes, PRP values, bio pages.

It does not replace a PCIe analyzer: there are no TLPs here.

## Requirements

- root (drgn reads `/proc/kcore`);
- Python 3 and drgn (`pip install drgn` or your distro package);
- **DWARF debug info** for the running kernel and the `nvme` / `nvme_core` modules.

drgn 0.3.0 uses DWARF, not BTF. BTF alone is not enough. Check:

```bash
uname -r
ls -l /usr/lib/debug/boot/vmlinux-$(uname -r)
ls -l /usr/lib/debug/lib/modules/$(uname -r)/
```

If these are missing, install the matching `-dbgsym` package from your distribution's debug repository. On Ubuntu, the kernel debug image is typically `linux-image-$(uname -r)-dbgsym` (a signed wrapper) or `linux-image-unsigned-$(uname -r)-dbgsym`. There is not always a separate `linux-modules-...-dbgsym`; check for module debug files after installation.

For a custom kernel, build with `CONFIG_DEBUG_INFO_DWARF5=y` (or DWARF4) and keep the unstripped `vmlinux` plus module `.ko` files.

### Ubuntu 24.04 example

A stock Ubuntu kernel is provided by `linux-image-$(uname -r)` and `linux-modules-$(uname -r)`, but **debug symbols are not in the normal archive**. They are published on `ddebs.ubuntu.com`; a stock install usually has no ddebs source, so `apt-cache policy ...-dbgsym` returns nothing until you add it.

```bash
. /etc/os-release
sudo tee /etc/apt/sources.list.d/ddebs.list >/dev/null <<EOF
deb http://ddebs.ubuntu.com ${VERSION_CODENAME} main restricted universe multiverse
deb http://ddebs.ubuntu.com ${VERSION_CODENAME}-updates main restricted universe multiverse
deb http://ddebs.ubuntu.com ${VERSION_CODENAME}-proposed main restricted universe multiverse
EOF

# The ddebs archive is signed by the Ubuntu Debug Symbol Archive key
# C8CAB6595FDFF622 (fingerprint F2ED C64D C5AE E1F6 B9C6 21F0 C8CA B659 5FDF F622).
# The keyring package is in the normal Ubuntu archive:
sudo apt-get install ubuntu-dbgsym-keyring

sudo apt-get update
apt-cache search "$(uname -r).*dbgsym"

sudo apt-get install linux-image-$(uname -r)-dbgsym
```

The signed wrapper is tiny and depends on `linux-image-unsigned-$(uname -r)-dbgsym`. If the exact ABI is not in `noble`/`noble-updates`, it may only be in `noble-proposed`; add that source line and rerun `apt-get update`. The 7.0.0-43 HWE debug image was found in `noble-proposed` during this experiment.

If `apt-get update` still reports `NO_PUBKEY C8CAB6595FDFF622`, the keyring package was not installed or the source line was added before it; install `ubuntu-dbgsym-keyring` and rerun `apt-get update`. Do not disable signature verification with `trusted=yes`.

Verify:

```bash
dpkg -L linux-image-unsigned-$(uname -r)-dbgsym | grep vmlinux
find /usr/lib/debug/lib/modules/$(uname -r) -name 'nvme*.ko*' 2>/dev/null
```

The image dbgsym package is large: the 7.0.0-43 unsigned package is about **2.1 GiB download / 2.4 GiB installed**. If module debug files are not included, drgn may still list block/NVMe core types from vmlinux but fail on `struct nvme_dev`; in that case use a kernel build that provides module DWARF or build the modules with debug info. BTF alone will not work with drgn 0.3.0.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r analysis/requirements-drgn.txt
```

or install drgn system-wide and use the `drgn` command.

## Usage

```bash
sudo .venv/bin/python analysis/drgn_nvme.py --list
sudo .venv/bin/python analysis/drgn_nvme.py --queues --limit 16 --raw-sqe
sudo .venv/bin/python analysis/drgn_nvme.py --requests --limit 8
sudo .venv/bin/python analysis/drgn_nvme.py --all --json > /tmp/nvme-state.json
```

`--list` prints namespaces, controller instance, queue count, `max_hw_sectors`, `max_segments`, `sqsize`.
`--queues` prints per-queue ring state and the last `--limit` SQE slots.
`--requests` walks busy requests with `request_queue_busy_iter()` and prints the NVMe command plus bio pages.
`--all` enables list + queues + requests + PRP metadata.

### PRP and IOMMU

By default `--iova-mode none`: PRP1/PRP2 are printed, but PRP list pages are **not** dereferenced.

- `--iova-mode direct`: treat PRP as a physical address and use the direct map. Only valid when the IOMMU is off, in passthrough, or identity-mapped.
- `--iova-mode kernel`: treat PRP as a kernel virtual address. This is for controlled experiments only.

The script prints the number of IOMMU groups from `/sys/kernel/iommu_groups`. If that is non-zero, `direct` is usually wrong: PRP values are IOVAs. The current script does not walk AMD-Vi / Intel VT-d page tables.

Ways to get a meaningful PRP list dump:

1. boot a test kernel with `amd_iommu=off` or `iommu=pt` (only if the device is identity-mapped), then use `--iova-mode direct`;
2. extend `prp_entries()` with an IOMMU page-table walker for your platform;
3. correlate the PRP IOVA with a separate IOMMU mapping dump.

The block-layer `bio_pages` output is always the real `struct page` mapping and does not depend on the IOMMU; use it to cross-check DMA lengths and physical pages.

## What is printed

### Namespace

```
nvme0n1 nsid=1 lba=512 ctrl=0x... ops=pcie queue_count=... max_hw_sectors=... max_segments=... sqsize=...
```

### Queue snapshot

```
qid=1 depth=1024 tail=... head=... sqes=64 sq_dma=0x... cq_dma=0x...
  slot=... write cid=... nsid=1 slba=... nlb=7 bytes=4096 prp1=0x... prp2=0x...
```

The queue snapshot reads the last `--limit` slots of the submission ring. If the queue was idle, these slots may be stale; in-flight requests are the authoritative live set.

### In-flight request

```
qid=1 tag=... sector=... len=4096 write cid=... nsid=1 slba=... nlb=7 bytes=4096 prp1=0x... prp2=0x... status=0
  prp: prp1=0x..., prp2=0x...
  bio page 0 phys=0x... virt=0x... off=0 len=4096
```

For 4KiB and below, `prp2=0` and one page is expected. For 8KiB, `prp2` is the second page. For 16KiB+, `prp2` is a PRP list (3/7/15/31 entries for 16/32/64/128KiB).

## Correlating with tracefs/bpftrace

- Match by `qid`, `cid`, `slba`, and `prp1/prp2`.
- `nvme_setup_cmd` gives qid/cid/nsid/slba/nlb; drgn gives the raw SQE and PRP fields.
- `block_rq_issue` gives the request sector/bytes; drgn `bio_pages` gives the corresponding pages.
- Use `--queues` to inspect SQ ring slots even when no trace was running.
- Use `--requests` during a fio run or a stall to see the exact in-flight command set.

## Limitations

- Live memory changes while reading; a snapshot can be inconsistent. Run it during a quiet moment or accept approximation.
- Driver structs are kernel-version-specific; use matching debug info.
- Only the PCIe transport is supported for queue details (`container_of(ctrl, struct nvme_dev, ctrl)`).
- Non-multipath setup is assumed. With NVMe multipath, `disk->private_data` may be `struct nvme_ns_head`, not `struct nvme_ns`; the script skips such disks. Extend `find_namespaces()` if needed.
- Fabrics transports (RDMA/TCP) do not share the PRP/queue layout; only generic request fields are meaningful.
- The script is read-only: it never writes kernel memory or issues NVMe commands.
- No PCIe TLP, no device firmware/FTL internals.
- IOMMU translation is not implemented; see above.

## Troubleshooting

- `Permission denied: '/proc/kcore'`: run as root.
- `missing type struct nvme_dev`: install module debug info, not just vmlinux.
- `not a PCIe nvme_dev`: the namespace is on a fabrics transport (RDMA/TCP); queue layout differs.
- `request_queue_busy_iter` error: check kernel version and block-layer debug info.
- Empty results: no in-flight I/O at the sampled instant; run during `fio` or use `--queues` for ring contents.
