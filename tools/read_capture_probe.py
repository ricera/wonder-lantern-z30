"""Decode WL-CAP.DBG from the capture probe: builder calls with descriptor dumps."""
import argparse
import json
import struct
from pathlib import Path

MAGIC, VERSION = 0x31504357, 1
BUILDERS = ('0x10141b48', '0x10141dc0', '0x1014237a', '0x10142bea', '0x1014319e', '0x10146f6a')
INFO = ('type', '+98', 'area208', '226', '227', '228', 'size229', '22a', '22b', '22c', '22d', '239')
DUMP = 0xD0


def pairs(dump):
    """Plausible (W, H) half-word pairs, as hints for reading the raw dump."""
    out = []
    for off in range(0, len(dump) - 3, 2):
        w, h = struct.unpack_from('<2H', dump, off)
        if 100 <= w <= 8192 and 100 <= h <= 8192:
            out.append((hex(off), w, h))
    return out


def decode(data):
    if len(data) < 72:
        raise ValueError('Log too short')
    magic, version, phase, status, seq, count, lock, _, dropped = struct.unpack_from('<9I', data, 0)
    capacity, stride, header = struct.unpack_from('<3I', data, 56)
    if magic != MAGIC or version != VERSION:
        raise ValueError(f'Not a version {VERSION} capture probe log')
    base = header - 8
    if len(data) != base + capacity * stride:
        raise ValueError(f'Unexpected log size {len(data)}')
    records = []
    for i in range(min(count, capacity)):
        r = data[base + i * stride:base + (i + 1) * stride]
        first, hits, last, rphase, builder, caller = struct.unpack_from('<6I', r, 0)
        if not first:
            continue
        info = r[24:24 + len(INFO)]
        dump = r[40:40 + DUMP]
        records.append(dict(first_seq=first, last_seq=last, hits=hits, phase=rphase,
                            builder=BUILDERS[builder] if builder < len(BUILDERS) else builder,
                            caller=hex(caller), info=dict(zip(INFO, info)),
                            dump=dump.hex(), size_pairs=pairs(dump)))
    records.sort(key=lambda r: r['first_seq'])
    return dict(version=version, phase=phase, status=status, sequence=seq, records_used=count,
                capacity=capacity, dropped=dropped, lock_word=lock, records=records)


def table(log):
    lines = [f"sequence {log['sequence']}  records {log['records_used']}/{log['capacity']}  "
             f"dropped {log['dropped']}  final phase {log['phase']}"]
    for r in sorted(log['records'], key=lambda r: (r['phase'], r['first_seq'])):
        i = r['info']
        lines.append(f"ph{r['phase']} #{r['first_seq']} x{r['hits']} {r['builder']} from {r['caller']}  "
                     f"type {i['type']} area {i['area208']} size {i['size229']} "
                     f"226-22d {[i[k] for k in ('226','227','228','22a','22b','22c','22d')]} 239 {i['239']}")
        lines.append('    ' + ' '.join(f"{o}:{w}x{h}" for o, w, h in r['size_pairs']))
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('log', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    log = decode(a.log.read_bytes())
    print(table(log))
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(log, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
