#!/usr/bin/env python3
import argparse
import collections
import csv
import json
import math
import os
import re

TRACE_RE = re.compile(r'^\s*(\S+?)-(\d+)\s+\[(\d+)\].*?(\d+\.\d+): (\w+): (.*)$')
BIO_RE = re.compile(r'(\d+,\d+) (\S+) (\d+) \+ (\d+) \[(.*)\]')
RQ_RE = re.compile(r'(\d+,\d+) (\S+) (\d+) \(\) (\d+) \+ (\d+) .*\[(.*)\]')
SETUP_IO_RE = re.compile(r'nvme\d+: disk=[^,]+, qid=(\d+), cmdid=(\d+), nsid=(\d+), flags=(0x[0-9a-f]+), meta=(0x[0-9a-f]+), cmd=\((nvme_cmd_\w+)(.*)\)')
SETUP_ADM_RE = re.compile(r'nvme\d+: qid=0, cmdid=(\d+), nsid=(\d+), flags=(0x[0-9a-f]+), meta=(0x[0-9a-f]+), cmd=\((nvme_admin_\w+)(.*)\)')
COMP_RE = re.compile(r'nvme\d+: (?:disk=[^,]+, )?qid=(\d+), cmdid=(\d+), res=(0x[0-9a-f]+), retries=(\d+), flags=(0x[0-9a-f]+), status=(0x[0-9a-f]+)')
SLBA_RE = re.compile(r'slba=(\d+), len=(\d+),')
ADM_BLOCK_RE = re.compile(r'(\d+\.\d+): block_rq_issue: \d+,\d+ N (\d+) ')


def bs_bytes(name):
    if name.endswith('k'):
        return int(float(name[:-1]) * 1024)
    if name.endswith('m'):
        return int(float(name[:-1]) * 1024 * 1024)
    return int(name)


def read_manifest(capture):
    rows = []
    path = os.path.join(capture, 'manifest.txt')
    with open(path) as f:
        for line in f:
            p = line.split()
            if len(p) >= 5:
                rows.append((p[0], p[1], int(p[2]), int(p[3]), int(p[4])))
    return rows


def parse_trace(path):
    out = {
        'bio': collections.Counter(),
        'req': collections.Counter(),
        'fio': collections.Counter(),
        'bg': collections.Counter(),
        'adm': collections.Counter(),
        'comp': collections.Counter(),
        'status': collections.Counter(),
        'adm_comp': collections.Counter(),
    }
    if not os.path.exists(path):
        return out
    with open(path, errors='replace') as f:
        for line in f:
            m = TRACE_RE.search(line)
            if not m:
                continue
            task, _, _, _, ev, rest = m.groups()
            if ev == 'block_bio_queue':
                mm = BIO_RE.match(rest)
                if mm and mm.group(5) == 'fio':
                    out['bio'][int(mm.group(4)) * 512] += 1
            elif ev == 'block_rq_issue':
                mm = RQ_RE.match(rest)
                if mm and mm.group(6) == 'fio':
                    out['req'][int(mm.group(3))] += 1
            elif ev == 'nvme_setup_cmd':
                a = SETUP_ADM_RE.search(rest)
                if a:
                    out['adm'][a.group(5)] += 1
                    continue
                i = SETUP_IO_RE.search(rest)
                if i:
                    op = i.group(6)
                    sl = SLBA_RE.search(i.group(7))
                    nlb = int(sl.group(2)) if sl else -1
                    (out['fio'] if task == 'fio' else out['bg'])[(op, nlb)] += 1
            elif ev == 'nvme_complete_rq':
                c = COMP_RE.search(rest)
                if c:
                    out['comp'][int(c.group(1))] += 1
                    out['status'][c.group(6)] += 1
    return out


def parse_fio(path, op):
    try:
        with open(path) as f:
            j = json.load(f)['jobs'][0][op]
        return {
            'ios': j['total_ios'],
            'bytes': j['io_kbytes'] * 1024,
            'iops': j['iops'],
            'bw_mib': j['bw_bytes'] / 1048576.0,
            'runtime_ms': j['runtime'],
            'error': j.get('error', 0),
        }
    except Exception:
        return {'ios': 0, 'bytes': 0, 'iops': 0, 'bw_mib': 0, 'runtime_ms': 0, 'error': 1}


def prp_info(bs, bytes_per_cmd):
    pages = max(1, math.ceil(bytes_per_cmd / 4096.0))
    if bytes_per_cmd <= 4096:
        mode = 'PRP1'
    elif bytes_per_cmd == 8192:
        mode = 'PRP1+PRP2'
    else:
        mode = 'PRP1+PRP2 list(%d)' % (pages - 1)
    return pages, mode


def parse_bpf(path, manifest):
    global_stats = {
        'rqs_calls': 0, 'sum_len': 0, 'hist': collections.Counter(),
        'sub': 0, 'rq': 0, 'sqe': 0,
    }
    phases = []
    for bs, op, t0, t1, _ in manifest:
        phases.append({
            'bs': bs, 'op': op, 't0': t0, 't1': t1,
            'rqs_calls': 0, 'sum_len': 0, 'hist': collections.Counter(),
            'sub': 0, 'rq': 0, 'sqe': 0,
        })

    def find(ts):
        for p in phases:
            if p['t0'] <= ts <= p['t1']:
                return p
        return None

    samples = []
    if not os.path.exists(path):
        return global_stats, phases, samples
    with open(path) as f:
        for line in f:
            m = re.match(r'RQS ts=(\d+) len=(\d+)', line)
            if m:
                ts = int(m.group(1)); ln = int(m.group(2))
                global_stats['rqs_calls'] += 1
                global_stats['sum_len'] += ln
                global_stats['hist'][ln] += 1
                p = find(ts)
                if p:
                    p['rqs_calls'] += 1
                    p['sum_len'] += ln
                    p['hist'][ln] += 1
                continue
            if line.startswith('SUB'):
                ts = int(re.search(r'ts=(\d+)', line).group(1))
                global_stats['sub'] += 1
                p = find(ts)
                if p:
                    p['sub'] += 1
                continue
            if line.startswith('RQ '):
                ts = int(re.search(r'ts=(\d+)', line).group(1))
                global_stats['rq'] += 1
                p = find(ts)
                if p:
                    p['rq'] += 1
                continue
            m = re.match(r'SQE ts=(\d+) src=(\w+) op=(\d+) nlb=(\d+) cid=(\d+) prp1=0x([0-9a-f]+) prp2=0x([0-9a-f]+)', line)
            if m:
                ts = int(m.group(1)); src = m.group(2); op = int(m.group(3)); nlb = int(m.group(4))
                cid = int(m.group(5)); prp1 = int(m.group(6), 16); prp2 = int(m.group(7), 16)
                global_stats['sqe'] += 1
                p = find(ts)
                if p:
                    p['sqe'] += 1
                if op == 0:
                    pages = 0
                else:
                    off = prp1 & 0xfff
                    pages = math.ceil((off + (nlb + 1) * 512) / 4096.0)
                samples.append({
                    'ts': ts,
                    'phase_bs': p['bs'] if p else '',
                    'phase_op': p['op'] if p else '',
                    'background': 0 if p else 1,
                    'src': src,
                    'op': op,
                    'nlb': nlb,
                    'cid': cid,
                    'prp1': '0x%x' % prp1,
                    'prp2': '0x%x' % prp2,
                    'prp2_nonzero': 1 if prp2 else 0,
                    'pages': pages,
                })
    return global_stats, phases, samples


def parse_admin(path):
    setups = []
    blocks = []
    comps = {}
    if not os.path.exists(path):
        return []
    with open(path, errors='replace') as f:
        for line in f:
            if 'nvme_setup_cmd' in line:
                m = SETUP_ADM_RE.search(line)
                if m:
                    ts = float(TRACE_RE.search(line).group(4))
                    setups.append({'ts': ts, 'cmdid': int(m.group(1)), 'opcode': m.group(5)})
            elif 'block_rq_issue' in line:
                m = ADM_BLOCK_RE.search(line)
                if m:
                    blocks.append({'ts': float(m.group(1)), 'bytes': int(m.group(2))})
            elif 'nvme_complete_rq' in line:
                m = COMP_RE.search(line)
                if m and int(m.group(1)) == 0:
                    comps[int(m.group(2))] = {'status': m.group(6), 'result': m.group(3)}
    rows = []
    for i, s in enumerate(setups):
        next_ts = setups[i + 1]['ts'] if i + 1 < len(setups) else float('inf')
        data_bytes = 0
        for b in blocks:
            if s['ts'] <= b['ts'] < next_ts:
                data_bytes = b['bytes']
                break
        c = comps.get(s['cmdid'], {'status': '', 'result': ''})
        rows.append({
            'opcode': s['opcode'],
            'cmdid': s['cmdid'],
            'data_bytes': data_bytes,
            'status': c['status'],
            'result': c['result'],
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--capture', required=True)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    capture = os.path.abspath(args.capture)
    out = os.path.abspath(args.out or capture)
    os.makedirs(out, exist_ok=True)
    manifest = read_manifest(capture)

    matrix = []
    for bs, op, t0, t1, rc in manifest:
        b = bs_bytes(bs)
        bytes_per_cmd = min(b, 131072)
        nlb = bytes_per_cmd // 512 - 1
        exp_op = 'nvme_cmd_write' if op == 'write' else 'nvme_cmd_read'
        pages, mode = prp_info(b, bytes_per_cmd)
        st = parse_trace(os.path.join(capture, 'trace_%s_%s.txt' % (bs, op)))
        fio = parse_fio(os.path.join(capture, 'fio_%s_%s.json' % (bs, op)), op)
        fio_op = sum(v for (o, n), v in st['fio'].items() if o == exp_op)
        fio_exp = sum(v for (o, n), v in st['fio'].items() if o == exp_op and n == nlb)
        fio_extra = fio_op - fio_exp
        fio_flush = sum(v for (o, n), v in st['fio'].items() if o == 'nvme_cmd_flush')
        bg_op = sum(v for (o, n), v in st['bg'].items() if o == exp_op)
        dma_bytes = sum((n + 1) * 512 for (o, n), v in st['fio'].items() if o == exp_op and n >= 0 for _ in range(v))
        bio_cnt = sum(st['bio'].values())
        req_cnt = sum(st['req'].values())
        req_bytes = sum(k * v for k, v in st['req'].items())
        status_nonzero = sum(v for k, v in st['status'].items() if k != '0x0')
        notes = []
        if fio_extra:
            notes.append('extra=%d cmds' % fio_extra)
        if b > 131072:
            notes.append('split=%dx128KiB' % (b // 131072))
        if bg_op:
            notes.append('bg=%d cmds' % bg_op)
        if fio_flush:
            notes.append('flush=%d' % fio_flush)
        if st['adm']:
            notes.append('admin=%d' % sum(st['adm'].values()))
        matrix.append({
            'bs': bs,
            'op': op,
            'fio_ios': fio['ios'],
            'fio_bytes': fio['bytes'],
            'fio_iops': round(fio['iops'], 1),
            'fio_bw_mib': round(fio['bw_mib'], 1),
            'fio_runtime_ms': fio['runtime_ms'],
            'fio_error': fio['error'],
            'block_bio': bio_cnt,
            'block_req': req_cnt,
            'block_req_bytes': req_bytes,
            'nvme_cmds': fio_op,
            'nvme_expected': fio_exp,
            'nvme_extra': fio_extra,
            'nvme_bg': bg_op,
            'flush_fio': fio_flush,
            'admin': sum(st['adm'].values()),
            'block_per_fio': round(req_cnt / fio['ios'], 3) if fio['ios'] else 0,
            'nvme_per_fio': round(fio_op / fio['ios'], 3) if fio['ios'] else 0,
            'nvme_per_block': round(fio_op / req_cnt, 3) if req_cnt else 0,
            'nlb': nlb,
            'bytes_per_cmd': bytes_per_cmd,
            'pages': pages,
            'prp_mode': mode,
            'dma_bytes': dma_bytes,
            'dma_amp': round(dma_bytes / fio['bytes'], 3) if fio['bytes'] else 0,
            'status_nonzero': status_nonzero,
            'notes': '; '.join(notes),
        })

    matrix_fields = list(matrix[0].keys()) if matrix else []
    with open(os.path.join(out, 'matrix_summary.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=matrix_fields)
        w.writeheader()
        w.writerows(matrix)
    with open(os.path.join(out, 'matrix_summary.json'), 'w') as f:
        json.dump(matrix, f, indent=2)

    gstats, phases, samples = parse_bpf(os.path.join(capture, 'nvme_matrix_bpf.log'), manifest)
    batch_rows = [{
        'scope': 'global', 'bs': 'all', 'op': 'all',
        'rqs_calls': gstats['rqs_calls'], 'sum_len': gstats['sum_len'],
        'avg_batch': round(gstats['sum_len'] / gstats['rqs_calls'], 3) if gstats['rqs_calls'] else 0,
        'len1': gstats['hist'].get(1, 0), 'len2': gstats['hist'].get(2, 0),
        'len_gt2': sum(v for k, v in gstats['hist'].items() if k > 2),
        'sub_calls': gstats['sub'],
        'cmds_per_submit': round(gstats['sum_len'] / gstats['sub'], 3) if gstats['sub'] else 0,
        'rq_singles': gstats['rq'], 'sqe_samples': gstats['sqe'],
    }]
    for p in phases:
        batch_rows.append({
            'scope': 'phase', 'bs': p['bs'], 'op': p['op'],
            'rqs_calls': p['rqs_calls'], 'sum_len': p['sum_len'],
            'avg_batch': round(p['sum_len'] / p['rqs_calls'], 3) if p['rqs_calls'] else 0,
            'len1': p['hist'].get(1, 0), 'len2': p['hist'].get(2, 0),
            'len_gt2': sum(v for k, v in p['hist'].items() if k > 2),
            'sub_calls': p['sub'],
            'cmds_per_submit': round(p['sum_len'] / p['sub'], 3) if p['sub'] else 0,
            'rq_singles': p['rq'], 'sqe_samples': p['sqe'],
        })
    with open(os.path.join(out, 'batch_summary.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(batch_rows[0].keys()))
        w.writeheader()
        w.writerows(batch_rows)
    if samples:
        with open(os.path.join(out, 'sqe_samples.csv'), 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(samples[0].keys()))
            w.writeheader()
            w.writerows(samples)

    admin = parse_admin(os.path.join(capture, 'trace_admin.txt'))
    if admin:
        with open(os.path.join(out, 'admin_summary.csv'), 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(admin[0].keys()))
            w.writeheader()
            w.writerows(admin)

    print('wrote summaries to', out)


if __name__ == '__main__':
    main()
