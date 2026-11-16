#!/usr/bin/env python3
import argparse
import csv
import math
import os

FONT = "'Helvetica Neue', Arial, sans-serif"
BLUE = '#2563eb'
ORANGE = '#f59e0b'
GREEN = '#10b981'
PURPLE = '#8b5cf6'
RED = '#dc2626'
GRAY = '#6b7280'
INK = '#111827'
LIGHT = '#f3f4f6'


def esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def num(v):
    return f'{int(v):,}' if float(v).is_integer() else f'{v:,.1f}'


def text(x, y, s, size=12, anchor='start', weight='normal', fill=INK, opacity=1.0):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" '
            f'text-anchor="{anchor}" font-weight="{weight}" fill="{fill}" opacity="{opacity}">{esc(s)}</text>')


def rect(x, y, w, h, fill, stroke='none', rx=0, opacity=1.0):
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(0.0, w):.1f}" height="{max(0.0, h):.1f}" '
            f'fill="{fill}" stroke="{stroke}" rx="{rx}" opacity="{opacity}"/>')


def line(x1, y1, x2, y2, stroke='#d1d5db', width=1, dash=''):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{stroke}" stroke-width="{width}"{d}/>')


def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img"><title>{esc(title)}</title>'
            f'<rect width="{w}" height="{h}" fill="#ffffff"/>{body}</svg>')


def read_csv(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def write(path, content):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w') as f:
        f.write(content)


def chart_layers(rows, out):
    w, h = 1080, 760
    left, right, top = 140, 1000, 110
    body = []
    body.append(text(left, 36, 'Fio IOs vs block requests vs NVMe commands (write)', 22, weight='bold'))
    body.append(text(left, 60, '16 MiB file, direct=1, iodepth=8; x axis is logarithmic', 13, fill=GRAY))
    body.append(text(780, 92, 'fio', 12, fill=BLUE, weight='bold'))
    body.append(rect(760, 82, 12, 12, BLUE))
    body.append(text(842, 92, 'block request', 12, fill=ORANGE, weight='bold'))
    body.append(rect(822, 82, 12, 12, ORANGE))
    body.append(text(956, 92, 'NVMe command', 12, fill=GREEN, weight='bold'))
    body.append(rect(936, 82, 12, 12, GREEN))
    vmin, vmax = 10.0, 100000.0
    def lx(v):
        v = max(vmin, float(v))
        return left + (math.log10(v) - math.log10(vmin)) / (math.log10(vmax) - math.log10(vmin)) * (right - left)
    for g in (10, 100, 1000, 10000, 100000):
        x = lx(g)
        body.append(line(x, top - 8, x, top + 12 * 44 - 6, '#e5e7eb'))
        body.append(text(x, top + 12 * 44 + 16, num(g), 11, anchor='middle', fill=GRAY))
    for i, r in enumerate(rows):
        y = top + i * 44
        if r['bs'] == '512':
            body.append(rect(left - 116, y - 20, right - left + 124, 34, '#fff7ed'))
        body.append(text(left - 12, y + 6, r['bs'], 13, anchor='end', weight='bold'))
        vals = [('fio', int(r['fio_ios']), BLUE, y - 15), ('block', int(r['block_req']), ORANGE, y - 4), ('nvme', int(r['nvme_cmds']), GREEN, y + 7)]
        for label, v, color, yy in vals:
            x = lx(v)
            body.append(rect(left, yy, x - left, 9, color, rx=2))
            body.append(text(x + 5, yy + 8, num(v), 10, fill=color, weight='bold'))
        if r['nvme_extra'] and r['nvme_extra'] != '0':
            body.append(text(960, y + 6, '+%s extra' % num(int(r['nvme_extra'])), 10, fill=RED, weight='bold'))
        elif int(r['bytes_per_cmd']) < int(r['fio_bytes']) and r['bs'] != '512':
            pass
    body.append(text(left, 700, '512B write adds 4,096 overlapping 3,584B writes (reported by fio, block layer and NVMe layer).', 12, fill=RED))
    body.append(text(left, 720, '256KiB and above are split by the block layer into 128KiB requests; read rows have the same shape except the 512B write anomaly.', 12, fill=GRAY))
    write(out, svg(w, h, ''.join(body), 'Fio versus block versus NVMe IO counts'))


def chart_ratio(rows, out):
    w, h = 1000, 660
    left, right, top = 140, 900, 110
    body = []
    body.append(text(left, 36, 'NVMe commands per fio IO (write)', 22, weight='bold'))
    body.append(text(left, 60, 'Block requests / NVMe commands = 1.00 in every row; bars show command count ratio', 13, fill=GRAY))
    vmax = 8.5
    def x(v): return left + v / vmax * (right - left)
    for g in (0, 1, 2, 4, 6, 8):
        xx = x(g)
        body.append(line(xx, top - 8, xx, top + 12 * 42 - 8, '#e5e7eb'))
        body.append(text(xx, top + 12 * 42 + 14, str(g), 11, anchor='middle', fill=GRAY))
    for i, r in enumerate(rows):
        y = top + i * 42
        ratio = float(r['nvme_per_fio'])
        color = GREEN if abs(ratio - 1.0) < 1e-9 else (ORANGE if ratio <= 2.0 else PURPLE)
        body.append(text(left - 12, y + 6, r['bs'], 13, anchor='end', weight='bold'))
        body.append(rect(left, y - 10, max(2.0, x(ratio) - left), 18, color, rx=3))
        body.append(text(x(ratio) + 6, y + 5, '%.2fx' % ratio, 11, fill=color, weight='bold'))
        if r['nvme_extra'] and r['nvme_extra'] != '0':
            body.append(text(x(ratio) + 56, y + 5, '+%s cmds' % num(int(r['nvme_extra'])), 10, fill=RED))
        if int(r['bytes_per_cmd']) < int(r['fio_bytes']) and r['bs'] != '512':
            pass
    body.append(text(left, 630, '1.00x: fio IO = block request = NVMe command. 2x/4x/8x: one 256KiB/512KiB/1MiB fio IO is split into 128KiB requests.', 12, fill=GRAY))
    write(out, svg(w, h, ''.join(body), 'NVMe commands per fio IO'))


def chart_dma(rows, out):
    w, h = 1000, 600
    left, right, top = 150, 900, 110
    body = []
    body.append(text(left, 36, 'DMA bytes / fio bytes (write)', 22, weight='bold'))
    body.append(text(left, 60, '512B write amplifies to 30.0 MiB for a 16.0 MiB fio workload', 13, fill=GRAY))
    vmax = 2.0
    def x(v): return left + v / vmax * (right - left)
    for g in (0.0, 0.5, 1.0, 1.5, 2.0):
        xx = x(g)
        body.append(line(xx, top - 8, xx, top + 12 * 38 - 6, '#e5e7eb'))
        body.append(text(xx, top + 12 * 38 + 16, '%.1fx' % g, 11, anchor='middle', fill=GRAY))
    for i, r in enumerate(rows):
        y = top + i * 38
        amp = float(r['dma_amp'])
        color = RED if amp > 1.001 else GREEN
        body.append(text(left - 12, y + 6, r['bs'], 13, anchor='end', weight='bold'))
        body.append(rect(left, y - 9, max(2.0, x(amp) - left), 16, color, rx=3))
        label = '%.3fx' % amp
        if amp > 1.001:
            label += '  (30 / 16 MiB)'
        body.append(text(x(amp) + 6, y + 5, label, 11, fill=color, weight='bold'))
    body.append(text(left, 565, 'Split at >128KiB does not amplify bytes: each 128KiB command carries one quarter of a 512KiB fio IO, etc.', 12, fill=GRAY))
    write(out, svg(w, h, ''.join(body), 'DMA bytes versus fio bytes'))


def chart_table(rows, out):
    w, h = 1060, 760
    cols = [(60, 'fio bs'), (190, 'NLB'), (300, 'bytes/cmd'), (450, 'pages/cmd'), (580, 'cmds/fio'), (720, 'PRP mode')]
    body = []
    body.append(text(60, 40, 'NVMe command size and PRP mode by fio block size (write)', 22, weight='bold'))
    body.append(text(60, 64, 'NLB is zero-based: bytes = (NLB + 1) x 512; 8KiB uses PRP2 as page 2; 16KiB+ uses a PRP list', 13, fill=GRAY))
    body.append(rect(48, 86, 964, 40, '#111827', rx=6))
    for x, name in cols:
        body.append(text(x + 8, 111, name, 12, fill='#ffffff', weight='bold'))
    for i, r in enumerate(rows):
        y = 126 + i * 42
        if i % 2 == 0:
            body.append(rect(48, y, 964, 38, LIGHT))
        if r['prp_mode'] != 'PRP1':
            body.append(rect(48, y, 964, 38, '#ecfdf5'))
        vals = [r['bs'], str(r['nlb']), num(int(r['bytes_per_cmd'])), str(r['pages']), '%.2f' % float(r['nvme_per_fio']), r['prp_mode']]
        for (x, _), v in zip(cols, vals):
            body.append(text(x + 8, y + 25, v, 13, fill=INK if x != 720 else GREEN, weight='bold' if x == 720 else 'normal'))
    body.append(text(60, 690, '512B write: 4,096 additional 3,584B commands (NLB=6, PRP1) overlap sectors 1-7 of every 4KiB block.', 12, fill=RED))
    body.append(text(60, 712, '256KiB/512KiB/1MiB: commands/fio = 2/4/8, each command is 128KiB with a 31-entry PRP list.', 12, fill=GRAY))
    write(out, svg(w, h, ''.join(body), 'NVMe NLB and PRP mode table'))


def chart_batch(batch, out):
    g = [r for r in batch if r['scope'] == 'global'][0]
    w, h = 1000, 540
    body = []
    body.append(text(60, 40, 'NVMe submission batching (all matrix runs)', 22, weight='bold'))
    body.append(text(60, 64, 'nvme_queue_rqs batches, submit calls and commands', 13, fill=GRAY))
    left, top = 210, 130
    vmin, vmax = 100.0, 200000.0
    def lx(v):
        v = max(vmin, float(v))
        return left + (math.log10(v) - math.log10(vmin)) / (math.log10(vmax) - math.log10(vmin)) * 690
    for gv in (100, 1000, 10000, 100000):
        x = lx(gv)
        body.append(line(x, top - 10, x, top + 160, '#e5e7eb'))
        body.append(text(x, top + 182, num(gv), 11, anchor='middle', fill=GRAY))
    bars = [('batch len 1', int(g['len1']), BLUE, top + 20), ('batch len 2', int(g['len2']), ORANGE, top + 90)]
    for label, v, color, y in bars:
        body.append(text(left - 14, y + 17, label, 14, anchor='end', weight='bold'))
        body.append(rect(left, y, lx(v) - left, 26, color, rx=4))
        body.append(text(lx(v) + 8, y + 18, num(v), 12, fill=color, weight='bold'))
    panel_y = 330
    body.append(rect(60, panel_y, 880, 150, '#f9fafb', stroke='#e5e7eb', rx=8))
    body.append(text(84, panel_y + 34, 'queue_rqs calls', 13, fill=GRAY))
    body.append(text(300, panel_y + 34, num(int(g['rqs_calls'])), 16, weight='bold'))
    body.append(text(84, panel_y + 66, 'NVMe commands', 13, fill=GRAY))
    body.append(text(300, panel_y + 66, num(int(g['sum_len'])), 16, weight='bold'))
    body.append(text(520, panel_y + 34, 'submit calls', 13, fill=GRAY))
    body.append(text(700, panel_y + 34, num(int(g['sub_calls'])), 16, weight='bold'))
    body.append(text(520, panel_y + 66, 'commands / submit', 13, fill=GRAY))
    body.append(text(700, panel_y + 66, '%.3f' % float(g['cmds_per_submit']), 16, weight='bold'))
    body.append(text(84, panel_y + 108, 'All 4,096 batch-length-2 entries are the 512B write extra 3,584B + 512B pairs; every other phase uses batch length 1.', 12, fill=GRAY))
    body.append(text(84, panel_y + 130, 'One submit/doorbell group per queue_rqs call -> only ~3% fewer doorbells than commands in this workload.', 12, fill=GRAY))
    write(out, svg(w, h, ''.join(body), 'NVMe batching and doorbell counts'))


def chart_throughput(rows, out):
    w, h = 1080, 560
    left, right, top, bottom = 80, 1030, 100, 470
    body = []
    body.append(text(left, 38, 'fio bandwidth by block size', 22, weight='bold'))
    body.append(text(left, 62, '16 MiB micro-run per point; indicative only, not a steady-state benchmark', 13, fill=GRAY))
    write_rows = {r['bs']: float(r['fio_bw_mib']) for r in rows if r['op'] == 'write'}
    read_rows = {r['bs']: float(r['fio_bw_mib']) for r in rows if r['op'] == 'read'}
    labels = [r['bs'] for r in rows if r['op'] == 'write']
    ymax = 3000.0
    for g in range(0, 3001, 500):
        y = bottom - g / ymax * (bottom - top)
        body.append(line(left, y, right, y, '#e5e7eb'))
        body.append(text(left - 8, y + 4, str(g), 11, anchor='end', fill=GRAY))
    step = (right - left) / (len(labels) - 1)
    def plot(vals, color, label, dy):
        pts = []
        for i, bs in enumerate(labels):
            x = left + i * step
            y = bottom - vals[bs] / ymax * (bottom - top)
            pts.append((x, y))
        for i in range(len(pts) - 1):
            body.append(line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], color, 2.5))
        for i, (x, y) in enumerate(pts):
            body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"/>')
            if labels[i] == '512':
                body.append(text(x + 6, y + dy, '%.1f' % vals[labels[i]], 10, fill=color, weight='bold'))
        body.append(rect(right - 180, 80 + (0 if color == BLUE else 24), 12, 12, color))
        body.append(text(right - 162, 91 + (0 if color == BLUE else 24), label, 12, fill=color, weight='bold'))
    plot(write_rows, BLUE, 'write', -8)
    plot(read_rows, ORANGE, 'read', 16)
    for i, bs in enumerate(labels):
        x = left + i * step
        body.append(text(x, bottom + 24, bs, 11, anchor='middle', fill=GRAY))
    body.append(text(left, 520, 'The 512B write point is low because 4,096 extra 3,584B NVMe writes double the DMA traffic.', 12, fill=GRAY))
    write(out, svg(w, h, ''.join(body), 'fio bandwidth by block size'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data')
    ap.add_argument('--out', default='charts')
    args = ap.parse_args()
    matrix = read_csv(os.path.join(args.data, 'matrix_summary.csv'))
    batch = read_csv(os.path.join(args.data, 'batch_summary.csv'))
    writes = [r for r in matrix if r['op'] == 'write']
    os.makedirs(args.out, exist_ok=True)
    chart_layers(writes, os.path.join(args.out, 'io_layers_by_bs.svg'))
    chart_ratio(writes, os.path.join(args.out, 'nvme_per_fio.svg'))
    chart_dma(writes, os.path.join(args.out, 'dma_amp.svg'))
    chart_table(writes, os.path.join(args.out, 'nlb_prp.svg'))
    chart_batch(batch, os.path.join(args.out, 'batch_doorbell.svg'))
    chart_throughput(matrix, os.path.join(args.out, 'throughput_by_bs.svg'))
    print('wrote charts to', args.out)


if __name__ == '__main__':
    main()
