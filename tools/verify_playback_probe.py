"""Offline checks for the playback probe: transparency, recording, menu actions.

Runs the patched and stock EG side by side in Unicorn and calls the generic
fit 0x10238C3A directly (5th argument on the stack) and through the live-view
display fit. The file service is the sound-settings fixture model. No camera.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]

FIT = 0x10238C3A
# source WH, target WH, rect, flags (bits seen in callers: 0x20, 0x8C3, 0)
CASES = [((6000, 4000), (720, 480), (0, 0, 720, 480), 0x20),
         ((1920, 1080), (720, 480), (0, 0, 720, 480), 0x20),
         ((6000, 4000), (720, 480), (0, 0, 720, 480), 0x8C3),
         ((4000, 6000), (720, 480), (0, 0, 720, 480), 0x20),
         ((3840, 2160), (1920, 1080), (0, 0, 1920, 1080), 0x8C3),
         ((640, 424), (720, 480), (40, 20, 680, 460), 0),
         ((5568, 3712), (720, 480), (0, 38, 720, 442), 0x40)]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--deps', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    if a.deps:
        sys.path.insert(0, str(a.deps.resolve()))
    from native_runtime import Context, STACK, STOP
    from verify_sound_settings import Fixture
    from verify_aspect_display_fit import fit as display_fit
    from build_playback_probe import build
    from read_playback_probe import decode
    from z30_extensions.container import extract_eg
    from z30_extensions.features import playback_probe as pp

    stock = extract_eg(a.source.read_bytes())
    image, report = build(stock)
    info = report['features']['playback_probe']
    fn = {k: int(v, 16) for k, v in info['functions'].items()}
    files = {}
    probe = Fixture(stock, image, report, files, properties={})
    native = Context(stock, stock)
    for ctx in (probe, native):
        ctx.map(0xC237C380, 0x400)
    state = probe.state + info['state_offset']
    word = lambda off: int.from_bytes(probe.u.mem_read(state + off, 4), 'little')
    put = lambda off, v: probe.u.mem_write(state + off, struct.pack('<I', v))
    log = lambda: decode(bytes(probe.u.mem_read(state + pp.DUMP_OFFSET, pp.DUMP_BYTES)))
    checks = []

    def check(name, condition, detail=None):
        checks.append(dict(name=name, ok=bool(condition), detail=detail))
        if not condition:
            raise AssertionError(f'{name}: {detail}')

    def gfit(ctx, src, dst, rect, flags):
        base = 0x500A0000
        ctx.u.mem_write(base, struct.pack('<2h', *src))
        ctx.u.mem_write(base + 0x10, struct.pack('<2h', *dst))
        ctx.u.mem_write(base + 0x20, struct.pack('<4h', *rect))
        ctx.u.mem_write(base + 0x30, b'\xA5' * 8)
        ctx.u.mem_write(STACK + 0xF0000, struct.pack('<I', flags) + b'\x5A' * 12)
        r = ctx.call(FIT, (base, base + 0x10, base + 0x20, base + 0x30))
        return r, struct.unpack('<4h', ctx.u.mem_read(base + 0x30, 8)), \
            bytes(ctx.u.mem_read(STACK + 0xF0000, 16))

    put(pp.H_MAGIC, 0)
    check('transparent before init', gfit(probe, *CASES[0]) == gfit(native, *CASES[0]))
    check('no record before init', word(pp.H_COUNT) == 0 and word(pp.H_SEQ) == 0)
    probe.call(fn['bitrate_restore'])
    check('restore initializes header', word(pp.H_MAGIC) == pp.MAGIC and word(pp.H_CAPACITY) == pp.CAPACITY)

    results = []
    for case in CASES:
        got, want = gfit(probe, *case), gfit(native, *case)
        results.append(dict(case=case, result=got[1]))
        check(f'generic fit identical {case}', got == want, (got, want))
    check('one record per case', word(pp.H_COUNT) == len(CASES), word(pp.H_COUNT))
    gfit(probe, *CASES[0]); gfit(probe, *CASES[0])
    first = log()['records'][0]
    check('dedup and fields', first['hits'] == 3 and first['caller'] == hex(STOP | 1) and
          first['flags'] == '0x20' and first['source_wh'] == [6000, 4000] and
          first['target_wh'] == [720, 480] and first['rect'] == [0, 0, 720, 480] and
          tuple(first['result']) == results[0]['result'], first)

    # Through the live-view display fit: identical rect, caller attributed.
    got, want = display_fit(probe, 2), display_fit(native, 2)
    check('live-view fit identical through wrapper', got == want, (got, want))
    named = [r for r in log()['records'] if r['caller'] == '0x1018af11']
    check('live-view caller recorded', len(named) == 1 and named[0]['caller_name'], named)

    count = word(pp.H_COUNT)
    probe.call(fn['dsq_phase'], (state,))
    gfit(probe, *CASES[1])
    check('phase tags new records', word(pp.H_PHASE) == 1 and word(pp.H_COUNT) == count + 1 and
          log()['records'][-1]['phase'] == 1)
    probe.call(fn['dsq_save'], (state,))
    saved = files.get('A:\\' + pp.LOG_NAME)
    check('save writes full log', word(pp.H_STATUS) == pp.S_SAVED and saved is not None and
          len(saved) == pp.DUMP_BYTES, None if saved is None else len(saved))
    check('saved log decodes', decode(saved)['records'] == log()['records'])
    put(pp.H_LOCK, 1)
    dropped = word(pp.H_DROPPED)
    check('locked: transparent, dropped', gfit(probe, *CASES[2]) == gfit(native, *CASES[2]) and
          word(pp.H_DROPPED) == dropped + 1)
    put(pp.H_LOCK, 0)
    probe.call(fn['dsq_clear'], (state,))
    check('clear empties log', word(pp.H_COUNT) == 0 and not log()['records'])
    for i in range(pp.CAPACITY + 5):
        gfit(probe, (6000, 4000 - i), (720, 480), (0, 0, 720, 480), 0x20)
    check('capacity bounded', word(pp.H_COUNT) == pp.CAPACITY and word(pp.H_DROPPED) >= 5)

    summary = dict(eg_sha256=report['sha256'], size=report['size'],
                   edits=[(e['name'], e['offset']) for e in report['edits']],
                   passed=sum(1 for c in checks if c['ok']), results=results, checks=checks)
    print(f"{summary['passed']} checks passed; EG sha256 {report['sha256']}")
    if a.output:
        a.output.write_text(json.dumps(summary, indent=2, default=str) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
