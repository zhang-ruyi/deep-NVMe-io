# Data

- `matrix_summary.csv` / `.json`: block-size matrix summary generated from the 2026-11-16 run.
- `batch_summary.csv`: `nvme_queue_rqs` batch lengths and submit-call counts.
- `sqe_samples.csv`: sampled SQE fields from the matrix run, assigned to a phase when possible.
- `sqe_full_4k.csv`: 508 decoded 64-byte SQEs from the separate 4K capture (255 writes, 253 reads; 4 lines with printable repr escapes were skipped).
- `admin_summary.csv`: qid 0 admin commands captured by the separate admin probe.
- `drgn_live.json`: one live drgn snapshot used by `charts/drgn_live_snapshot.svg`.
- `manifest.txt`: monotonic start/end timestamps for each matrix phase.
- `fio/`: fio JSON output for each `bs`/`op`.
- `device/`: queue limits, controller/namespace identify, PCIe config and run metadata.

Raw trace files and test binaries are not committed. The full matrix capture is about 120 MiB; regenerate it with `scripts/bs_matrix.sh`.
