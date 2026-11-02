"""Offline checks for the capture probe: builder transparency, recording, menu actions.

Runs each complete capture-stage builder in the patched and stock EG with
synthetic capture-info records (area 0, sizes L/M/S). Builders whose native
dependencies are outside the fixture are reported as not modelled.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    from native_runtime import Context, STOP
    from verify_sound_settings import Fixture
    from build_capture_probe import build
    from read_capture_probe import decode
    from z30_extensions.container import extract_eg
    from z30_extensions.features import capture_probe as cp

    stock = extract_eg(a.source.read_bytes())
    image, report = build(stock)
    info = report['features']['capture_probe']
    fn = {k: int(v, 16) for k, v in info['functions'].items()}
    files = {}
    probe = Fixture(stock, image, report, files, properties={})
    native = Context(stock, stock)
    state = probe.state + info['state_offset']
    word = lambda off: int.from_bytes(probe.u.mem_read(state + off, 4), 'little')
    put = lambda off, v: probe.u.mem_write(state + off, struct.pack('<I', v))
    log = lambda: decode(bytes(probe.u.mem_read(state + cp.DUMP_OFFSET, cp.DUMP_BYTES)))
    checks = []

    def check(name, condition, detail=None):
        checks.append(dict(name=name, ok=bool(condition), detail=detail))
        if not condition:
            raise AssertionError(f'{name}: {detail}')

    obj, rec, dims, holder = 0x500A0000, 0x500A1000, 0x500A8000, 0x500A9000

    def run(ctx, address, size, extra=0):
        ctx.u.mem_write(obj, bytes(0x200))
        ctx.u.mem_write(rec, bytes(0x3000))
        ctx.u.mem_write(rec + 0x229, bytes([size]))
        ctx.u.mem_write(rec + 0x239, bytes([extra]))
        ctx.u.mem_write(obj + 4, struct.pack('<I', holder))
        ctx.u.mem_write(holder, struct.pack('<I', 0x500B0000))
        ctx.u.mem_write(dims, struct.pack('<2H', 6000, 4000))
        r = ctx.call(address, (obj, rec, dims, 0))
        return r, bytes(ctx.u.mem_read(obj, 0x200))

    put(cp.H_MAGIC, 0)
    probe.call(fn['bitrate_restore'])
    check('restore initializes header', word(cp.H_MAGIC) == cp.MAGIC)
    modelled = []
    for index, (address, *_rest) in enumerate(cp.BUILDERS):
        for size in (0, 1, 2):
            try:
                want = run(native, address, size)
            except Exception as error:
                checks.append(dict(name=f'builder {address:#x} size {size}', ok=None,
                                   detail=f'not modelled: {error}'))
                continue
            before = word(cp.H_COUNT)
            got = run(probe, address, size)
            check(f'builder {address:#x} size {size} identical', got == want)
            new = [r for r in log()['records'] if r['builder'] == hex(address) and r['info']['size229'] == size]
            check(f'builder {address:#x} size {size} recorded', word(cp.H_COUNT) == before + 1 and len(new) == 1 and
                  new[0]['caller'] == hex(STOP | 1) and new[0]['dump'] == got[1][:cp.DUMP].hex(), new)
            modelled.append((hex(address), size))
    check('at least four builders modelled', len({m[0] for m in modelled}) >= 4, modelled)
    address = cp.BUILDERS[[hex(b[0]) for b in cp.BUILDERS].index(modelled[0][0])][0]
    run(probe, address, modelled[0][1])
    hits = [r for r in log()['records'] if r['builder'] == modelled[0][0] and r['info']['size229'] == modelled[0][1]]
    check('repeat call deduplicated', len(hits) == 1 and hits[0]['hits'] == 2, hits)
    probe.call(fn['dsq_phase'], (state,))
    run(probe, address, modelled[0][1])
    check('phase separates records', log()['records'][-1]['phase'] == 1)
    probe.call(fn['dsq_save'], (state,))
    saved = files.get('A:\\' + cp.LOG_NAME)
    check('save writes full log', saved is not None and len(saved) == cp.DUMP_BYTES and
          decode(saved)['records'] == log()['records'])
    probe.call(fn['dsq_clear'], (state,))
    check('clear empties log', word(cp.H_COUNT) == 0)
    for i in range(cp.CAPACITY + 3):
        run(probe, address, modelled[0][1], extra=i + 1)
    check('capacity bounded', word(cp.H_COUNT) == cp.CAPACITY and word(cp.H_DROPPED) >= 1,
          (word(cp.H_COUNT), word(cp.H_DROPPED)))
    summary = dict(eg_sha256=report['sha256'], modelled=modelled,
                   passed=sum(1 for c in checks if c['ok']),
                   not_modelled=[c for c in checks if c['ok'] is None], checks=checks)
    print(f"{summary['passed']} checks passed, {len(summary['not_modelled'])} not modelled; EG sha256 {report['sha256']}")
    for c in summary['not_modelled']:
        print(' ', c['name'], '->', c['detail'][:100])
    if a.output:
        a.output.write_text(json.dumps(summary, indent=2, default=str) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
