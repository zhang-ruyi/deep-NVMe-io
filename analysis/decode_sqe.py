#!/usr/bin/env python3
import argparse
import csv
import math
import os
import re


def parse_repr(text):
    out = bytearray()
    i = 0
    while i < len(text):
        c = text[i]
        if c == '\\' and i + 1 < len(text):
            n = text[i + 1]
            if n == 'x' and i + 3 < len(text):
                try:
                    out.append(int(text[i + 2:i + 4], 16))
                    i += 4
                    continue
                except ValueError:
                    pass
            esc = {'\\': 0x5c, '"': 0x22, 'n': 0x0a, 'r': 0x0d, 't': 0x09}.get(n)
            if esc is not None:
                out.append(esc)
                i += 2
                continue
        out.append(ord(c))
        i += 1
    return bytes(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--log', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    rows = []
    with open(args.log, errors='replace') as f:
        for line in f:
            m = re.search(r'SQE src=(\w+) ts=(\d+) cpu=(\d+) op=(\d+) (.*)$', line.rstrip('\n'))
            if not m:
                continue
            b = parse_repr(m.group(5))
            if len(b) != 64:
                continue
            op = b[0]
            nlb = int.from_bytes(b[48:50], 'little')
            prp1 = int.from_bytes(b[24:32], 'little')
            prp2 = int.from_bytes(b[32:40], 'little')
            if op == 0:
                pages = 0
            else:
                pages = math.ceil(((prp1 & 0xfff) + (nlb + 1) * 512) / 4096.0)
            rows.append({
                'ts': int(m.group(2)),
                'src': m.group(1),
                'op': op,
                'cid': int.from_bytes(b[2:4], 'little'),
                'nsid': int.from_bytes(b[4:8], 'little'),
                'slba': int.from_bytes(b[40:48], 'little'),
                'nlb': nlb,
                'prp1': '0x%x' % prp1,
                'prp2': '0x%x' % prp2,
                'prp2_nonzero': 1 if prp2 else 0,
                'ctrl': int.from_bytes(b[50:52], 'little'),
                'dsmgmt': int.from_bytes(b[52:56], 'little'),
                'reftag': int.from_bytes(b[56:60], 'little'),
                'pages': pages,
            })
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ['ts'])
        w.writeheader()
        w.writerows(rows)
    print('decoded', len(rows), 'SQEs ->', args.out)


if __name__ == '__main__':
    main()
