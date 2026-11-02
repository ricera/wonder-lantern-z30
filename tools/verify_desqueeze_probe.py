"""Offline checks for the desqueeze probe: transparency, recording, menu actions.

Runs the patched and stock EG side by side in Unicorn. Fit arguments are the
bounded fixtures of verify_aspect_display_fit, not camera object dumps; the
file service is the sound-settings fixture model. No camera, ISP or LCD.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]

VIEW_OBJECT = 0xC237C380
FIT_CASES = [  # area, viewport, control WH, flags
    (2, (720, 480), (720, 480), (0, 0)), (0, (720, 480), (720, 480), (0, 0)),
    (4, (720, 480), (720, 480), (0, 0)), (2, (480, 720), (480, 720), (0, 0)),
    (2, (720, 480), (720, 480), (1, 0)), (2, (720, 480), (640, 480), (0, 1)),
    (2, (1920, 1080), (1920, 1080), (0, 0)), (0, (640, 480), (640, 480), (0, 0)),
]
CLASS_CASES = [(5568, 3712), (5568, 3128), (4952, 3712), (4640, 3712), (3712, 3712),
               (5568, 2784), (720, 480), (1, 1), (3000, 1)]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--deps', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    if a.deps:
        sys.path.insert(0, str(a.deps.resolve()))
    from native_runtime import Context, STOP
    from verify_sound_settings import Fixture
    from verify_aspect_display_fit import fit
    from build_desqueeze_probe import build
    from read_desqueeze_probe import decode
    from z30_extensions.container import extract_eg
    from z30_extensions.features import desqueeze_probe as dp

    stock = extract_eg(a.source.read_bytes())
    image, report = build(stock)
    info = report['features']['desqueeze_probe']
    fn = {k: int(v, 16) for k, v in info['functions'].items()}
    files = {}
    probe = Fixture(stock, image, report, files, properties={})
    native = Context(stock, stock)
    for ctx in (probe, native):
        ctx.map(VIEW_OBJECT, 0x400)
        ctx.u.mem_write(VIEW_OBJECT + 0x5B, bytes([3, 5]))
    state = probe.state + info['state_offset']
    word = lambda off: int.from_bytes(probe.u.mem_read(state + off, 4), 'little')
    put = lambda off, v: probe.u.mem_write(state + off, struct.pack('<I', v))
    log = lambda: decode(bytes(probe.u.mem_read(state + dp.DUMP_OFFSET, dp.DUMP_BYTES)))
    checks = []

    def check(name, condition, detail=None):
        checks.append(dict(name=name, ok=bool(condition), detail=detail))
        if not condition:
            raise AssertionError(f'{name}: {detail}')

    # Uninitialized state: hooks must stay transparent and record nothing.
    put(dp.H_MAGIC, 0)
    r = fit(probe, *FIT_CASES[0][:1], viewport=FIT_CASES[0][1], control_wh=FIT_CASES[0][2])
    check('fit transparent before init', r == fit(native, 2), r)
    check('no record before init', word(dp.H_COUNT) == 0 and word(dp.H_SEQ) == 0)
    probe.call(fn['bitrate_restore'])
    check('restore initializes header', word(dp.H_MAGIC) == dp.MAGIC and word(dp.H_HEADER) == dp.HEADER)
    put(dp.H_PHASE, 0)
    probe.call(fn['bitrate_restore'])
    check('restore idempotent', word(dp.H_MAGIC) == dp.MAGIC)

    # 1. Fit transparency and one record per distinct call.
    results = []
    for area, viewport, control, flags in FIT_CASES:
        got = fit(probe, area, viewport, control, flags)
        want = fit(native, area, viewport, control, flags)
        results.append(dict(area=area, viewport=viewport, flags=flags, rect=got))
        check(f'fit identical area={area} vp={viewport} flags={flags}', got == want, (got, want))
    check('one record per distinct case', word(dp.H_COUNT) == len(FIT_CASES), word(dp.H_COUNT))
    for _ in range(3):
        fit(probe, 2)
    first = log()['records'][0]
    check('repeat call deduplicated', word(dp.H_COUNT) == len(FIT_CASES) and first['hits'] == 4, first)
    check('record fields', first['caller'] == hex(STOP | 1) and first['area'] == 2 and
          first['ratio_class_name'] == '16:9' and first['image_wh'] == [5568, 3128] and
          first['viewport_wh'] == [720, 480] and first['display_rect'] == [0, 36, 720, 442] and
          first['cluster_mask'] is None and first['view_object_5b_5c'] == [3, 5], first)
    flagged = [x for x in log()['records'] if x['flags'] != [0, 0]]
    check('flagged path has no image query but still a class',
          all(x['image_wh'] and x['ratio_class'] != 255 for x in flagged), flagged)

    # 2. Classifier transparency outside the fit (no pending pairing).
    put(dp.H_PENDING_ARG, 0)
    for wh in CLASS_CASES:
        for ctx in (probe, native):
            ctx.u.mem_write(0x50088000, struct.pack('<2h', *wh))
        got, want = probe.call(0x101D60CA, (0x50088000,)), native.call(0x101D60CA, (0x50088000,))
        check(f'classify identical {wh}', got == want, (got, want))
    check('classify outside fit leaves no pending pair', word(dp.H_PENDING_ARG) == 0)

    # 3. Caller cluster: mask observer plus original body, mask 0 and 1.
    for mask in (0, 1, 2, 4):
        for ctx in (probe, native):
            ctx.u.mem_write(0x50070000, bytes(0x400))
        try:
            probe.call(0x1018AF6C, (0x50070000, mask))
            native.call(0x1018AF6C, (0x50070000, mask))
        except Exception as error:  # Object pointers beyond the fixture.
            checks.append(dict(name=f'cluster mask {mask}', ok=None, detail=f'not modelled: {error}'))
            continue
        same = bytes(probe.u.mem_read(VIEW_OBJECT, 0x400)) == bytes(native.u.mem_read(VIEW_OBJECT, 0x400))
        check(f'cluster mask {mask} observed', word(dp.H_MASK) == mask and word(dp.H_MASK_LR) == STOP | 1)
        check(f'cluster mask {mask} view object identical', same)
    cluster = [x for x in log()['records'] if x['cluster_mask'] is not None]
    checks.append(dict(name='cluster-owned fit records', ok=True,
                       detail=[(x['caller_name'], x['cluster_mask'], x['display_rect']) for x in cluster]))

    # 4. Menu actions: phase, save, busy, clear.
    count = word(dp.H_COUNT)
    probe.call(fn['dsq_phase'], (state,))
    fit(probe, 2)
    check('phase tags new records', word(dp.H_PHASE) == 1 and word(dp.H_STATUS) == 1 and
          word(dp.H_COUNT) == count + 1 and log()['records'][-1]['phase'] == 1)
    for _ in range(12):
        probe.call(fn['dsq_phase'], (state,))
    check('phase saturates', word(dp.H_PHASE) == dp.MAX_PHASE)
    probe.call(fn['dsq_save'], (state,))
    saved = files.get('A:\\' + dp.LOG_NAME)
    check('save writes full log', word(dp.H_STATUS) == dp.S_SAVED and saved is not None and
          len(saved) == dp.DUMP_BYTES, None if saved is None else len(saved))
    check('saved log decodes', decode(saved)['records'] == log()['records'])
    check('lock released after save', word(dp.H_LOCK) == 0)
    put(dp.H_LOCK, 1)
    dropped = word(dp.H_DROPPED)
    r = fit(probe, 0, (640, 360), (640, 360))
    check('locked: fit transparent, event dropped', r == fit(native, 0, (640, 360), (640, 360)) and
          word(dp.H_DROPPED) == dropped + 1)
    probe.call(fn['dsq_save'], (state,))
    check('locked: save reports busy', word(dp.H_STATUS) == dp.S_BUSY)
    put(dp.H_LOCK, 0)
    probe.call(fn['dsq_clear'], (state,))
    check('clear empties log', word(dp.H_COUNT) == 0 and word(dp.H_PHASE) == 0 and
          word(dp.H_STATUS) == dp.S_CLEARED and not log()['records'])

    # 5. Capacity: distinct calls beyond the table are counted, not written.
    for i in range(dp.CAPACITY + 5):
        fit(probe, 2, (720, 480 - 2 * i), (720, 480 - 2 * i))
    check('capacity bounded', word(dp.H_COUNT) == dp.CAPACITY and word(dp.H_DROPPED) >= 5,
          (word(dp.H_COUNT), word(dp.H_DROPPED)))
    check('state stays within allocation', len(bytes(probe.u.mem_read(state, dp.STATE_BYTES))) == dp.STATE_BYTES)

    summary = dict(eg_sha256=report['sha256'], size=report['size'],
                   edits=[(e['name'], e['offset']) for e in report['edits']],
                   passed=sum(1 for c in checks if c['ok']),
                   not_modelled=[c for c in checks if c['ok'] is None], fit_results=results,
                   checks=checks)
    print(f"{summary['passed']} checks passed, {len(summary['not_modelled'])} not modelled; "
          f"EG sha256 {report['sha256']}")
    for c in checks:
        if c['ok'] is None or c['name'] == 'cluster-owned fit records':
            print(' ', c['name'], '->', c['detail'])
    if a.output:
        a.output.write_text(json.dumps(summary, indent=2, default=str) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
