#!/usr/bin/env python3
import argparse
import json
import math
import os
import sys

import drgn
from drgn import cast
from drgn.helpers.linux.block import blk_mq_rq_to_pdu, disk_name, request_queue_busy_iter
from drgn.helpers.linux.list import list_for_each_entry
from drgn.helpers.linux.mm import container_of, page_to_phys, page_to_virt, phys_to_virt

PAGE_SIZE = 4096
OP_NAMES = {
    0x00: 'flush',
    0x01: 'write',
    0x02: 'read',
    0x04: 'write_uncor',
    0x05: 'compare',
    0x08: 'write_zeroes',
    0x09: 'dsm',
    0x0c: 'verify',
    0x0d: 'reservation_register',
    0x11: 'reservation_report',
    0x19: 'copy',
}
ADMIN_NAMES = {
    0x01: 'delete_sq',
    0x02: 'create_sq',
    0x05: 'delete_cq',
    0x06: 'create_cq',
    0x08: 'get_log_page',
    0x09: 'set_features',
    0x0a: 'get_features',
    0x0c: 'async_event',
    0x0d: 'ns_management',
    0x0e: 'fw_commit',
    0x10: 'fw_download',
    0x11: 'device_self_test',
    0x14: 'ns_attachment',
    0x15: 'keep_alive',
    0x18: 'directive_send',
    0x19: 'directive_receive',
    0x1a: 'virtual_mgmt',
    0x1c: 'doorbell_buffer_config',
    0x1d: 'set_features',
}


def as_int(obj):
    try:
        return int(obj.value_())
    except Exception:
        return int(obj)


def as_text(obj):
    if isinstance(obj, bytes):
        return obj.decode('utf-8', 'replace')
    try:
        return obj.string_().decode('utf-8', 'replace')
    except Exception:
        return str(obj)


def read_bytes(prog, addr, size):
    return bytes(prog.read(addr, size))


def read_u64(prog, addr):
    return int.from_bytes(read_bytes(prog, addr, 8), 'little')


def op_name(op, admin=False):
    if admin:
        return ADMIN_NAMES.get(op, 'admin_0x%02x' % op)
    return OP_NAMES.get(op, 'op_0x%02x' % op)


def parse_sqe(buf, lba_shift, admin=False):
    op = buf[0]
    cid = int.from_bytes(buf[2:4], 'little')
    nsid = int.from_bytes(buf[4:8], 'little')
    prp1 = int.from_bytes(buf[24:32], 'little')
    prp2 = int.from_bytes(buf[32:40], 'little')
    nlb = int.from_bytes(buf[48:50], 'little')
    out = {
        'opcode': op,
        'op_name': op_name(op, admin),
        'cid': cid,
        'nsid': nsid,
        'prp1': '0x%x' % prp1,
        'prp2': '0x%x' % prp2,
        'admin': int(admin),
        'raw': buf.hex(),
    }
    if not admin:
        out.update({
            'slba': int.from_bytes(buf[40:48], 'little'),
            'nlb': nlb,
            'bytes': 0 if op == 0 else ((nlb + 1) << lba_shift if nlb >= 0 else 0),
            'control': int.from_bytes(buf[50:52], 'little'),
            'dsmgmt': int.from_bytes(buf[52:56], 'little'),
            'reftag': int.from_bytes(buf[56:60], 'little'),
        })
    return out


def iommu_groups():
    try:
        return sum(1 for _ in os.scandir('/sys/kernel/iommu_groups'))
    except Exception:
        return 0


def prp_entries(prog, cmd, mode, max_entries=64):
    prp1 = int(cmd['prp1'], 16)
    prp2 = int(cmd['prp2'], 16)
    data_bytes = int(cmd.get('bytes', 0))
    if data_bytes <= 0:
        return []
    offset = prp1 & (PAGE_SIZE - 1)
    pages = max(1, math.ceil((offset + data_bytes) / PAGE_SIZE))
    result = [{'idx': 0, 'addr': '0x%x' % prp1, 'raw': '0x%x' % prp1, 'kind': 'prp1'}]
    if pages == 1:
        return result
    if pages == 2:
        result.append({'idx': 1, 'addr': '0x%x' % prp2, 'raw': '0x%x' % prp2, 'kind': 'prp2'})
        return result
    if mode == 'none':
        result.append({'idx': 1, 'addr': 'untranslated', 'raw': '0x%x' % prp2, 'kind': 'prp_list'})
        return result
    if mode == 'kernel':
        base = prp2
    else:
        base = as_int(phys_to_virt(prp2))
    for i in range(min(pages - 1, max_entries)):
        try:
            raw = read_u64(prog, base + i * 8)
        except Exception as exc:
            result.append({'idx': i + 1, 'addr': 'read_error', 'raw': '0x%x' % prp2, 'kind': 'prp_list', 'error': str(exc)})
            break
        result.append({'idx': i + 1, 'addr': '0x%x' % raw, 'raw': '0x%x' % raw, 'kind': 'prp_list'})
    return result


def bio_pages(prog, rq, limit=8):
    out = []
    try:
        bio = rq.bio
        if not bio:
            return out
        vec = bio.bi_io_vec
        n = int(bio.bi_vcnt)
        for i in range(min(n, limit)):
            bv = vec[i]
            page = bv.bv_page
            phys = as_int(page_to_phys(page))
            virt = as_int(page_to_virt(page))
            out.append({
                'idx': i,
                'phys': '0x%x' % phys,
                'virt': '0x%x' % virt,
                'offset': int(bv.bv_offset),
                'len': int(bv.bv_len),
            })
    except Exception as exc:
        out.append({'error': str(exc)})
    return out


def find_namespaces(prog):
    namespaces = []
    seen = set()
    try:
        subsystems = prog['nvme_subsystems'].address_of_()
    except Exception:
        return namespaces
    try:
        subsys_iter = list_for_each_entry('struct nvme_subsystem', subsystems, 'entry')
    except Exception:
        return namespaces
    for subsys in subsys_iter:
        try:
            ctrls = list_for_each_entry('struct nvme_ctrl', subsys.ctrls.address_of_(), 'subsys_entry')
        except Exception:
            continue
        for ctrl in ctrls:
            ctrl_addr = as_int(ctrl)
            ops_name = ''
            try:
                ops_name = as_text(ctrl.ops.name)
            except Exception:
                pass
            try:
                ns_iter = list_for_each_entry('struct nvme_ns', ctrl.namespaces.address_of_(), 'list')
            except Exception:
                continue
            for ns in ns_iter:
                try:
                    disk = ns.disk
                    if not disk:
                        continue
                    name = as_text(disk_name(disk))
                    if (ctrl_addr, name) in seen:
                        continue
                    seen.add((ctrl_addr, name))
                    head = ns.head
                    lba_shift = int(head.lba_shift) if head else 9
                    nsid = int(head.ns_id) if head else 0
                    namespaces.append({
                        'disk': name,
                        'nsid': nsid,
                        'lba_shift': lba_shift,
                        'ctrl': ctrl_addr,
                        'ops': ops_name,
                        'ns': ns,
                        'ctrl_obj': ctrl,
                    })
                except Exception:
                    continue
    return namespaces


def controller_summary(prog, ns_info):
    ctrl = ns_info['ctrl_obj']
    out = {
        'addr': ns_info['ctrl'],
        'ops': ns_info['ops'],
        'instance': None,
        'queue_count': None,
        'max_hw_sectors': None,
        'max_segments': None,
        'sqsize': None,
    }
    try:
        out['instance'] = int(ctrl.instance)
        out['queue_count'] = int(ctrl.queue_count)
        out['max_hw_sectors'] = int(ctrl.max_hw_sectors)
        out['max_segments'] = int(ctrl.max_segments)
        out['sqsize'] = int(ctrl.sqsize)
    except Exception:
        pass
    return out


def queue_summary(prog, ns_info, limit, prp_mode, raw, lba_map):
    ctrl = ns_info['ctrl_obj']
    out = []
    try:
        dev = container_of(ctrl, 'struct nvme_dev', 'ctrl')
    except Exception as exc:
        return [{'error': 'not a PCIe nvme_dev: %s' % exc}]
    try:
        queues = dev.queues
        max_qid = int(dev.max_qid)
        online = int(dev.online_queues)
        qdepth = int(dev.q_depth)
    except Exception as exc:
        return [{'error': str(exc)}]
    limit = max(1, limit)
    for i in range(min(max_qid + 1, 1024)):
        try:
            q = queues[i]
            qid = int(q.qid)
            depth = int(q.q_depth) or qdepth
            raw_sqes = int(q.sqes)
            if raw_sqes in (6, 7):
                stride = 1 << raw_sqes
            elif raw_sqes in (64, 128):
                stride = raw_sqes
            else:
                stride = 64
            item = {
                'qid': qid,
                'depth': depth,
                'sq_tail': int(q.sq_tail),
                'cq_head': int(q.cq_head),
                'sqes': stride,
                'sq_dma_addr': '0x%x' % int(q.sq_dma_addr),
                'cq_dma_addr': '0x%x' % int(q.cq_dma_addr),
                'q_db': '0x%x' % as_int(q.q_db),
                'entries': [],
            }
            if q.sq_cmds:
                tail = int(q.sq_tail)
                start = (tail - limit) % depth if depth else 0
                for j in range(limit):
                    idx = (start + j) % depth if depth else 0
                    addr = cast('char *', q.sq_cmds) + idx * stride
                    try:
                        buf = read_bytes(prog, addr, 64)
                        admin = (qid == 0)
                        nsid = int.from_bytes(buf[4:8], 'little')
                        lba_shift = 9 if admin else lba_map.get(nsid, ns_info['lba_shift'])
                        cmd = parse_sqe(buf, lba_shift, admin=admin)
                        if not raw:
                            cmd.pop('raw', None)
                        if not admin and prp_mode != 'none':
                            cmd['prp_entries'] = prp_entries(prog, cmd, prp_mode)
                        cmd['slot'] = idx
                        item['entries'].append(cmd)
                    except Exception as exc:
                        item['entries'].append({'slot': idx, 'error': str(exc)})
            out.append(item)
        except Exception:
            continue
    return out


def request_summary(prog, ns_info, prp_mode, limit, raw, lba_map):
    out = []
    q = ns_info.get('ns').queue
    if not q:
        return out
    try:
        reqs = request_queue_busy_iter(q)
    except Exception as exc:
        return [{'error': str(exc)}]
    for rq in reqs:
        try:
            item = {
                'qid': None,
                'tag': int(rq.tag),
                'sector': int(rq.__sector),
                'data_len': int(rq.__data_len),
                'bio_pages': [],
            }
            try:
                hctx = rq.mq_hctx
                if hctx:
                    nvq = cast('struct nvme_queue *', hctx.driver_data)
                    if nvq:
                        item['qid'] = int(nvq.qid)
            except Exception:
                pass
            nvme_req = cast('struct nvme_request *', blk_mq_rq_to_pdu(rq))
            item['status'] = int(nvme_req.status)
            item['retries'] = int(nvme_req.retries)
            cmd_ptr = nvme_req.cmd
            if cmd_ptr:
                buf = read_bytes(prog, cmd_ptr, 64)
                nsid = int.from_bytes(buf[4:8], 'little')
                lba_shift = lba_map.get(nsid, ns_info['lba_shift'])
                cmd = parse_sqe(buf, lba_shift, admin=False)
                if not raw:
                    cmd.pop('raw', None)
                cmd['prp_entries'] = prp_entries(prog, cmd, prp_mode) if prp_mode != 'none' else []
                item['cmd'] = cmd
            item['bio_pages'] = bio_pages(prog, rq, limit=limit)
            out.append(item)
        except Exception as exc:
            out.append({'error': str(exc)})
    return out


def main():
    ap = argparse.ArgumentParser(description='Dump NVMe driver queues and in-flight requests from the live Linux kernel with drgn.')
    ap.add_argument('--list', action='store_true', help='list NVMe namespaces, controllers and queues')
    ap.add_argument('--queues', action='store_true', help='dump queue state and recent SQE slots')
    ap.add_argument('--requests', action='store_true', help='dump in-flight NVMe requests and bio pages')
    ap.add_argument('--all', action='store_true', help='same as --list --queues --requests --prp')
    ap.add_argument('--prp', action='store_true', help='resolve PRP entries when --iova-mode is not none')
    ap.add_argument('--iova-mode', choices=('none', 'direct', 'kernel'), default='none',
                    help='PRP address interpretation: none (print only), direct (IOMMU off/identity), kernel (treat as kernel VA)')
    ap.add_argument('--limit', type=int, default=8, help='queue slots / bio pages per item')
    ap.add_argument('--raw-sqe', action='store_true', help='include raw 64-byte SQE hex')
    ap.add_argument('--json', action='store_true', help='emit JSON')
    args = ap.parse_args()
    if not (args.list or args.queues or args.requests or args.all):
        args.list = True
    if args.all:
        args.list = args.queues = args.requests = True
        args.prp = True
    groups = iommu_groups()
    if args.prp and args.iova_mode == 'none':
        print('warning: --prp with --iova-mode none prints PRP values without dereferencing; use --iova-mode direct/kernel to read PRP lists', file=sys.stderr)
    if args.iova_mode == 'direct' and groups:
        print('warning: IOMMU groups detected; PRP values are IOVAs, --iova-mode direct may be wrong', file=sys.stderr)

    try:
        prog = drgn.get_default_prog()
    except drgn.NoDefaultProgramError:
        try:
            prog = drgn.program_from_kernel()
        except Exception as exc:
            print('drgn could not open the live kernel: %s' % exc, file=sys.stderr)
            print('run as root and check /proc/kcore permissions', file=sys.stderr)
            return 1
        try:
            prog.load_debug_info(default=True)
        except drgn.MissingDebugInfoError as exc:
            print('warning: missing debug info: %s' % exc, file=sys.stderr)
        except Exception:
            pass

    for name in ('struct nvme_ns', 'struct nvme_dev', 'struct nvme_queue', 'struct nvme_request'):
        try:
            prog.type(name)
        except LookupError:
            print('missing type %s; install vmlinux and nvme/nvme_core debug info' % name, file=sys.stderr)
            print('see docs/drgn.md', file=sys.stderr)
            return 1

    namespaces = find_namespaces(prog)
    lba_map = {n['nsid']: n['lba_shift'] for n in namespaces}
    data = {
        'iommu_groups': groups,
        'iova_mode': args.iova_mode,
        'namespaces': [],
        'queues': [],
        'requests': [],
    }
    seen_ctrl = set()
    seen_queue = set()
    for ns_info in namespaces:
        ns_out = {
            'disk': ns_info['disk'],
            'nsid': ns_info['nsid'],
            'lba_shift': ns_info['lba_shift'],
            'lba_size': 1 << ns_info['lba_shift'],
            'ctrl': ns_info['ctrl'],
            'ctrl_summary': controller_summary(prog, ns_info),
        }
        data['namespaces'].append(ns_out)
        if args.queues and ns_info['ctrl'] not in seen_ctrl:
            seen_ctrl.add(ns_info['ctrl'])
            data['queues'].append({
                'disk': ns_info['disk'],
                'queues': queue_summary(prog, ns_info, args.limit, args.iova_mode if args.prp else 'none', args.raw_sqe, lba_map),
            })
        if args.requests:
            try:
                qaddr = as_int(ns_info['ns'].queue)
            except Exception:
                qaddr = None
            if qaddr is None or qaddr not in seen_queue:
                if qaddr is not None:
                    seen_queue.add(qaddr)
                data['requests'].append({
                    'disk': ns_info['disk'],
                    'requests': request_summary(prog, ns_info, args.iova_mode if args.prp else 'none', args.limit, args.raw_sqe, lba_map),
                })

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    print('iommu_groups=%d iova_mode=%s' % (groups, args.iova_mode))
    if args.list:
        print('\nnamespaces:')
        for ns in data['namespaces']:
            c = ns['ctrl_summary']
            print('  %-10s nsid=%-3d lba=%-4d ctrl=0x%x ops=%s queue_count=%s max_hw_sectors=%s max_segments=%s sqsize=%s' % (
                ns['disk'], ns['nsid'], ns['lba_size'], ns['ctrl'], c.get('ops'),
                c.get('queue_count'), c.get('max_hw_sectors'), c.get('max_segments'), c.get('sqsize')))
    if args.queues:
        print('\nqueues:')
        for group in data['queues']:
            print(' ', group['disk'])
            for q in group['queues']:
                if 'error' in q:
                    print('    error:', q['error'])
                    continue
                print('    qid=%-3d depth=%-4d tail=%-4d head=%-4d sqes=%-3d sq_dma=%s cq_dma=%s' % (
                    q['qid'], q['depth'], q['sq_tail'], q['cq_head'], q['sqes'], q['sq_dma_addr'], q['cq_dma_addr']))
                for e in q['entries']:
                    if 'error' in e:
                        print('      slot error:', e['error'])
                        continue
                    if e['admin']:
                        print('      slot=%-3d %-16s cid=%-5d nsid=%d prp1=%s prp2=%s' % (
                            e['slot'], e['op_name'], e['cid'], e['nsid'], e['prp1'], e['prp2']))
                    else:
                        print('      slot=%-3d %-16s cid=%-5d nsid=%-3d slba=%-12d nlb=%-4d bytes=%-6d prp1=%s prp2=%s' % (
                            e['slot'], e['op_name'], e['cid'], e['nsid'], e['slba'], e['nlb'], e['bytes'], e['prp1'], e['prp2']))
    if args.requests:
        print('\nin-flight requests:')
        for group in data['requests']:
            print(' ', group['disk'])
            for r in group['requests']:
                if 'error' in r:
                    print('    error:', r['error'])
                    continue
                cmd = r.get('cmd')
                if cmd:
                    print('    qid=%-3s tag=%-4d sector=%-12d len=%-6d %-8s cid=%-5d nsid=%-3d slba=%-12d nlb=%-4d bytes=%-6d prp1=%s prp2=%s status=%s' % (
                        r.get('qid'), r['tag'], r['sector'], r['data_len'], cmd['op_name'], cmd['cid'], cmd['nsid'],
                        cmd['slba'], cmd['nlb'], cmd['bytes'], cmd['prp1'], cmd['prp2'], r.get('status')))
                    if cmd.get('prp_entries'):
                        print('      prp:', ', '.join('%s=%s' % (e['kind'], e['addr']) for e in cmd['prp_entries']))
                else:
                    print('    qid=%-3s tag=%-4d sector=%-12d len=%-6d no command pointer' % (
                        r.get('qid'), r['tag'], r['sector'], r['data_len']))
                for p in r.get('bio_pages', []):
                    if 'error' in p:
                        print('      bio error:', p['error'])
                    else:
                        print('      bio page %d phys=%s virt=%s off=%d len=%d' % (
                            p['idx'], p['phys'], p['virt'], p['offset'], p['len']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
