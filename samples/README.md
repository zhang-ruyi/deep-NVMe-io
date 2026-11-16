# Samples

Small excerpts from the capture, kept for format documentation:

- `trace_4k_write_head.txt`: block/NVMe trace lines for a 4K write phase.
- `trace_512_write_head.txt`: 512B write phase; note the 3,584-byte bios.
- `trace_admin.txt`: qid 0 admin commands from `nvme id-ctrl`, `nvme smart-log` and `nvme get-feature`.
- `nvme_matrix_bpf_head.txt`: `nvme_queue_rqs` batch lines and sampled SQE lines.
- `nvme_sqe_head.txt`: full 64-byte SQE dump from the separate 4K capture.
