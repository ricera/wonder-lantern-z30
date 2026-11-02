"""Decode WL-PBK.DBG from the playback probe into a table and optional JSON."""
import argparse
import json
import struct
from pathlib import Path

MAGIC, VERSION = 0x31425057, 1
HEADER, KEY_OFFSET = 80, 12
# Return addresses of known 0x10238C3A callers.
CALLERS = {0x1018AF11: 'live-view display fit 0x1018ADF0'}


def decode(data):
    """The file starts at state offset 8 (magic); offsets below are file offsets."""
    if len(data) < 72:
        raise ValueError('Log too short')
    magic, version, phase, status, seq, count, lock, _, dropped = struct.unpack_from('<9I', data, 0)
    capacity, stride, header = struct.unpack_from('<3I', data, 56)
    if magic != MAGIC or version != VERSION:
        raise ValueError(f'Not a version {VERSION} playback probe log')
    base = header - 8
    if len(data) != base + capacity * stride:
        raise ValueError(f'Unexpected log size {len(data)}')
    records = []
    for i in range(min(count, capacity)):
        r = data[base + i * stride:base + (i + 1) * stride]
        first, hits, last, rphase, caller, flags, src, dst = struct.unpack_from('<8I', r, 0)
        if not first:
            continue
        rect = struct.unpack_from('<4h', r, 32)
        out = struct.unpack_from('<4h', r, 40)
        w, h = out[2] - out[0], out[3] - out[1]
        records.append(dict(
            first_seq=first, last_seq=last, hits=hits, phase=rphase, caller=hex(caller),
            caller_name=CALLERS.get(caller, ''), flags=hex(flags),
            source_wh=[src & 65535, src >> 16], target_wh=[dst & 65535, dst >> 16],
            rect=list(rect), result=list(out), result_wh=[w, h],
            result_aspect=round(w / h, 4) if h else None))
    records.sort(key=lambda r: r['first_seq'])
    return dict(version=version, phase=phase, status=status, sequence=seq, records_used=count,
                capacity=capacity, dropped=dropped, lock_word=lock, records=records)


def table(log):
    lines = [f"sequence {log['sequence']}  records {log['records_used']}/{log['capacity']}  "
             f"dropped {log['dropped']}  final phase {log['phase']}",
             f"{'ph':>2} {'first':>6} {'last':>6} {'hits':>6} {'caller':10} {'flags':>6} "
             f"{'source':>11} {'target':>11} {'rect':22} {'result':22} aspect  name"]
    for r in sorted(log['records'], key=lambda r: (r['phase'], r['first_seq'])):
        lines.append(f"{r['phase']:>2} {r['first_seq']:>6} {r['last_seq']:>6} {r['hits']:>6} {r['caller']:10} "
                     f"{r['flags']:>6} {'%dx%d' % tuple(r['source_wh']):>11} {'%dx%d' % tuple(r['target_wh']):>11} "
                     f"{str(tuple(r['rect'])):22} {str(tuple(r['result'])):22} {r['result_aspect']}  {r['caller_name']}")
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
