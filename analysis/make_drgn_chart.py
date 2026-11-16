#!/usr/bin/env python3
import argparse
import json
import os

FONT = "'Helvetica Neue', Arial, sans-serif"
INK = '#111827'
GRAY = '#6b7280'
BLUE = '#2563eb'
GREEN = '#10b981'
ORANGE = '#f59e0b'
RED = '#dc2626'
LIGHT = '#f9fafb'


def esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def text(x, y, s, size=13, anchor='start', weight='normal', fill=INK):
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'text-anchor="{anchor}" font-weight="{weight}" fill="{fill}">{esc(s)}</text>')


def rect(x, y, w, h, fill=None, stroke='#d1d5db', rx=10):
    f = f' fill="{fill}"' if fill else ' fill="none"'
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}"{f} stroke="{stroke}" stroke-width="1.2"/>'


def line(x1, y1, x2, y2, color='#9ca3af', width=2, marker=True):
    m = ' marker-end="url(#arrow)"' if marker else ''
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}"{m}/>'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data/drgn_live.json')
    ap.add_argument('--out', default='charts')
    args = ap.parse_args()
    d = json.load(open(args.data))
    r = d['request']
    ns = d['namespace']
    qs = d['queues']
    admin = [q for q in qs if q['qid'] == 0]
    ioq = [q for q in qs if q['qid'] != 0]
    io_depth = sorted({q['depth'] for q in ioq})
    tails = [q['tail'] for q in ioq]
    idle = all(q['tail'] == q['head'] for q in ioq)

    body = []
    body.append(text(60, 42, 'Live drgn snapshot: request -> SQE -> PRP -> page', 23, weight='bold'))
    body.append(text(60, 66, 'Read from the running kernel while fio 4KiB QD8 was active; IOMMU is on', 13, fill=GRAY))

    body.append(rect(60, 100, 980, 105, LIGHT))
    body.append(text(82, 128, 'block request', 15, weight='bold', fill=BLUE))
    body.append(text(82, 154, 'qid=%s  tag=%s  op=%s  sector=%s  len=%s' % (r['qid'], r['tag'], r['op'], f"{r['sector']:,}", r['data_len']), 15))
    body.append(text(82, 180, 'request sector is the block-layer start; the SQE below is the command currently in the driver', 12, fill=GRAY))

    body.append(line(550, 205, 550, 245))
    body.append(rect(60, 250, 620, 170, LIGHT))
    body.append(text(82, 280, 'NVMe SQE (64 bytes)', 15, weight='bold', fill=GREEN))
    body.append(text(82, 308, 'op=%s  cid=%s  nsid=%s' % (r['op'], r['cid'], r['nsid']), 15))
    body.append(text(82, 334, 'SLBA=%s  NLB=%s  bytes=%s' % (f"{r['slba']:,}", r['nlb'], r['bytes']), 15))
    body.append(text(82, 360, 'PRP1=%s  PRP2=%s' % (r['prp1'], r['prp2']), 15))
    body.append(text(82, 390, 'sector != SLBA here: live reads are not atomic (see note)', 12, fill=RED))

    body.append(line(680, 330, 740, 330))
    body.append(rect(740, 245, 300, 175, '#fff7ed', stroke='#fdba74'))
    body.append(text(762, 275, 'IOMMU', 15, weight='bold', fill=ORANGE))
    body.append(text(762, 302, 'PRP1 is an IOVA', 13))
    body.append(text(762, 326, '0xcb379000 -> ?', 13))
    body.append(text(762, 352, 'script uses --iova-mode none', 12, fill=GRAY))
    body.append(text(762, 374, 'so no IOVA -> PA translation', 12, fill=GRAY))
    body.append(text(762, 400, 'IOMMU groups=%d' % d['iommu_groups'], 12, fill=ORANGE))

    body.append(line(740, 420, 680, 480))
    body.append(rect(60, 480, 620, 120, LIGHT))
    body.append(text(82, 510, 'block data page (bio_vec)', 15, weight='bold', fill=BLUE))
    body.append(text(82, 538, 'phys=%s  offset=%s  len=%s' % (r['bio_page']['phys'], r['bio_page']['offset'], r['bio_page']['len']), 15))
    body.append(text(82, 566, 'PRP1 IOVA and this physical page are different because IOMMU is on', 12, fill=GRAY))

    body.append(rect(740, 450, 300, 150, LIGHT))
    body.append(text(762, 480, 'queue snapshot', 15, weight='bold', fill=INK))
    body.append(text(762, 506, 'qid0 admin: depth=%s' % admin[0]['depth'], 13))
    body.append(text(762, 530, 'qid1..15 io: depth=%s' % ','.join(str(x) for x in io_depth), 13))
    body.append(text(762, 554, 'tail==head: %s' % ('yes' if idle else 'no'), 13))
    body.append(text(762, 578, 'tail range: %d..%d' % (min(tails), max(tails)), 13))

    body.append(text(60, 650, 'Snapshot values: sector=%s, SLBA=%s, PRP1=%s, bio phys=%s.' % (
        f"{r['sector']:,}", f"{r['slba']:,}", r['prp1'], r['bio_page']['phys']), 12, fill=GRAY))
    body.append(text(60, 672, 'For strict sector/SLBA correlation use tracefs; for PRP-list contents enable an IOMMU-aware translator.', 12, fill=GRAY))

    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="710" viewBox="0 0 1100 710" role="img">'
           '<title>Live drgn NVMe request snapshot</title>'
           '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
           '<path d="M 0 0 L 10 5 L 0 10 z" fill="#9ca3af"/></marker></defs>'
           '<rect width="1100" height="710" fill="#ffffff"/>' + ''.join(body) + '</svg>')
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, 'drgn_live_snapshot.svg')
    open(path, 'w').write(svg)
    print('wrote', path)


if __name__ == '__main__':
    main()
