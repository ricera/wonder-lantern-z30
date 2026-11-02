"""Decode WL-DSQ.DBG from the desqueeze probe into a table and optional JSON."""
import argparse
import json
import struct
import sys
from pathlib import Path

MAGIC, VERSION = 0x31514457, 1
CLASSES = {0: '3:2', 1: '16:9', 2: '5:4', 3: '4:3', 4: '1:1', 255: '-'}
# Return addresses of the nine static 0x1018ADF0 call sites.
CALLERS = {0x1018AFDB: 'cluster A mask&5 #1', 0x1018AFF5: 'cluster A mask&5 #2',
           0x1018B025: 'cluster A mask&6 #1', 0x1018B041: 'cluster A mask&6 #2',
           0x10196DA5: 'cluster B #1', 0x10196DCD: 'cluster B #2',
           0x10196E1D: 'cluster B #3', 0x10196E3D: 'cluster B #4', 0x1019848B: 'site C'}


def decode(data):
    """The file starts at state offset 8 (magic); offsets below are file offsets."""
    if len(data) < 72:
        raise ValueError('Log too short')
    magic, version, phase, status, seq, count, lock, _, dropped, mask, mask_lr = \
        struct.unpack_from('<11I', data, 0)
    capacity, stride, header = struct.unpack_from('<3I', data, 56)
    if magic != MAGIC or version != VERSION:
        raise ValueError(f'Not a version {VERSION} desqueeze probe log')
    base = header - 8
    if len(data) != base + capacity * stride:
        raise ValueError(f'Unexpected log size {len(data)}')
    records = []
    for i in range(min(count, capacity)):
        r = data[base + i * stride:base + (i + 1) * stride]
        first, hits, last, rphase, caller, packed, word10, image, viewport = struct.unpack_from('<9I', r, 0)
        if not first:
            continue
        view = struct.unpack_from('<4h', r, 36)
        ratio = struct.unpack_from('<2I', r, 44)
        rect = struct.unpack_from('<4h', r, 52)
        control = struct.unpack_from('<6H', r, 60)
        cmask, vbytes = struct.unpack_from('<2I', r, 72)
        cls = packed >> 24
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        records.append(dict(
            first_seq=first, last_seq=last, hits=hits, phase=rphase,
            caller=hex(caller), caller_name=CALLERS.get(caller, 'direct/other'),
            flags=[packed & 255, packed >> 8 & 255], area=packed >> 16 & 255,
            ratio_class=cls, ratio_class_name=CLASSES.get(cls, str(cls)),
            image_wh=[image & 65535, image >> 16] if image else None, word_10=hex(word10),
            viewport_wh=[viewport & 65535, viewport >> 16], view_rect=list(view),
            ratio=list(ratio), display_rect=list(rect), display_wh=[w, h],
            display_aspect=round(w / h, 4) if h else None, control=list(control),
            cluster_mask=None if cmask == 0xFFFFFFFF else cmask,
            view_object_5b_5c=[vbytes & 255, vbytes >> 8 & 255]))
    records.sort(key=lambda r: r['first_seq'])
    return dict(version=version, phase=phase, status=status, sequence=seq, records_used=count,
                capacity=capacity, dropped=dropped, lock_word=lock,
                last_cluster_mask=mask, last_cluster_caller=hex(mask_lr), records=records)


def table(log):
    lines = [f"sequence {log['sequence']}  records {log['records_used']}/{log['capacity']}  "
             f"dropped {log['dropped']}  final phase {log['phase']}"]
    head = ('ph', 'first', 'last', 'hits', 'caller', 'site', 'fl', 'ar', 'cls', 'image WH',
            'viewport', 'display rect', 'aspect', 'mask')
    lines.append('{:>2} {:>7} {:>7} {:>5} {:10} {:20} {:4} {:>2} {:>4} {:>10} {:>9} {:20} {:>6} {:>4}'.format(*head))
    for r in log['records']:
        image = 'x'.join(map(str, r['image_wh'])) if r['image_wh'] else '-'
        lines.append('{:>2} {:>7} {:>7} {:>5} {:10} {:20} {:4} {:>2} {:>4} {:>10} {:>9} {:20} {:>6} {:>4}'.format(
            r['phase'], r['first_seq'], r['last_seq'], r['hits'], r['caller'], r['caller_name'],
            f"{r['flags'][0]}/{r['flags'][1]}", r['area'], r['ratio_class_name'], image,
            'x'.join(map(str, r['viewport_wh'])), str(tuple(r['display_rect'])),
            r['display_aspect'] if r['display_aspect'] is not None else '-',
            '-' if r['cluster_mask'] is None else r['cluster_mask']))
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('log', type=Path)
    p.add_argument('--output', type=Path, help='Write decoded JSON here')
    a = p.parse_args()
    log = decode(a.log.read_bytes())
    print(table(log))
    if a.output:
        a.output.write_text(json.dumps(log, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    sys.exit(main())
