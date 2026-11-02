"""Offline checks for the desqueeze trial (v6): geometry, NV storage, relayout, previews, JPEG.

Runs the trial and stock EG side by side in Unicorn. The fit inputs replay the
final photo and movie live-view records of the camera probe log
(build/research/desqueeze-probe-v1.json); the stock run must reproduce the
camera's rectangles before trial results are trusted. The caller of the fit is
simulated by entering it with the camera return address and stopping there.
No camera, ISP or LCD.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]

FIT = 0x1018ADF0
LCD, LCD_STILL, HDMI, HDMI_STILL = 0x1018AFDB, 0x1018AFF5, 0x1018B025, 0x1018B041

# (name, area, viewport, view rect, control (W,H,l,t,r,b), camera rect)
CAMERA = [
    ('photo LCD', 0, (720, 480), (0, 0, 720, 480), (720, 480, 0, 0, 720, 480), (0, 0, 720, 480)),
    ('movie LCD', 2, (720, 480), (0, 38, 720, 442), (720, 480, 0, 38, 720, 442), (0, 38, 720, 442)),
    ('movie LCD (first pass)', 2, (720, 480), (0, 0, 720, 480), (720, 480, 0, 0, 720, 480), (0, 36, 720, 442)),
    ('photo HDMI', 0, (1920, 1080), (264, 28, 1656, 956), (720, 480, 0, 0, 720, 480), (264, 28, 1656, 956)),
    ('movie HDMI', 2, (1920, 1080), (264, 28, 1656, 956), (720, 480, 0, 38, 720, 442), (264, 100, 1656, 884)),
]


def expected(view, image_wh, num, den):
    """Ideal centred letterbox of a W*num/den : H picture in the view rect."""
    l, t, r, b = view
    w, h = r - l, b - t
    aspect = image_wh[0] * num / (image_wh[1] * den)
    if aspect >= w / h:
        dh = w / aspect
        return (l, t + (h - dh) / 2, r, b - (h - dh) / 2)
    dw = h * aspect
    return (l + (w - dw) / 2, t, r - (w - dw) / 2, b)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--deps', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    if a.deps:
        sys.path.insert(0, str(a.deps.resolve()))
    from unicorn import UC_HOOK_CODE
    from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R3
    from native_runtime import Context, STOP
    from verify_sound_settings import Fixture
    from build_desqueeze_trial import build
    from z30_extensions.container import extract_eg
    from z30_extensions.features import desqueeze_ui as du

    stock = extract_eg(a.source.read_bytes())
    image, report = build(stock)
    info = report['features']['desqueeze']
    fn = {k: int(v, 16) for k, v in info['functions'].items()}
    files = {}
    trial = Fixture(stock, image, report, files, properties={})
    native = Context(stock, stock)
    caller = {'lr': None}
    for ctx in (trial, native):
        ctx.map(0xC237C380, 0x400)
        ctx.map(du.NV_GROUP_RAM & ~0xFFF, 0x1000)

        def enter(u, address, size, data):
            if caller['lr'] is not None:
                u.reg_write(UC_ARM_REG_LR, caller['lr'])

        def leave(u, address, size, data):
            if caller['lr'] is not None and address == caller['lr'] & ~1:
                u.reg_write(UC_ARM_REG_PC, STOP | 1)
        ctx.u.hook_add(UC_HOOK_CODE, enter, begin=FIT, end=FIT)
        for ret in (LCD, LCD_STILL, HDMI, HDMI_STILL):
            ctx.u.hook_add(UC_HOOK_CODE, leave, begin=ret & ~1, end=ret & ~1)
    nv = lambda: bytes(trial.u.mem_read(du.NV_ADDRESS, 2))
    group = lambda: bytes(trial.u.mem_read(du.NV_GROUP_RAM, du.NV_GROUP_SIZE))
    trial.u.mem_write(du.NV_GROUP_RAM, bytes(range(1, du.NV_GROUP_SIZE + 1)))  # Neighbour sentinel.
    trial.u.mem_write(du.NV_ADDRESS, bytes(2))

    def select(index):
        trial.call(fn['dsq_nv_write'], (index,))
    checks = []

    def check(name, condition, detail=None):
        checks.append(dict(name=name, ok=bool(condition), detail=detail))
        if not condition:
            raise AssertionError(f'{name}: {detail}')

    def fit(ctx, ret, area, viewport, view, control):
        arg, ctl, size, vrect, ratio, result = [0x50080000 + i * 256 for i in range(6)]
        data = bytearray(32)
        data[0x14] = area
        struct.pack_into('<4I', data, 4, ctl, size, vrect, 0x8C3)
        struct.pack_into('<2I', data, 0x18, ratio, result)
        ctx.u.mem_write(arg, bytes(data))
        ctx.u.mem_write(ctl, struct.pack('<6H', *control) + bytes(12))
        ctx.u.mem_write(size, struct.pack('<2H', *viewport))
        ctx.u.mem_write(vrect, struct.pack('<4H', *view))
        ctx.u.mem_write(ratio, struct.pack('<2I', 1, 1))
        ctx.u.mem_write(result, b'\xA5' * 8)
        caller['lr'] = ret
        try:
            ctx.call(FIT, (arg,))
        finally:
            caller['lr'] = None
        return tuple(struct.unpack('<4h', ctx.u.mem_read(result, 8)))

    image_wh = {0: (5568, 3712), 2: (5568, 3128)}
    neighbours = group()
    check('build-time storage check', info['nv_storage']['keys'] == 34, info['nv_storage'])

    # 0. NV encoding: only index + check byte is accepted; others untouched.
    for index in range(len(du.NAMES)):
        select(index)
        want = bytes(2) if index == 0 else bytes([index, index ^ 0xFF])
        check(f'nv write {index}', nv() == want and trial.call(fn['dsq_nv_read']) == index, nv().hex())
        g = group()
        off = du.NV_ADDRESS - du.NV_GROUP_RAM
        check(f'nv write {index} leaves group neighbours', g[:off] + g[off + 2:] == neighbours[:off] + neighbours[off + 2:])
    select(9)
    check('out-of-range write stores OFF', nv() == bytes(2))
    for raw in (b'\x01\x00', b'\x05\xfa', b'\xff\x00', b'\x02\xfe', b'\xa5\xa5'):
        trial.u.mem_write(du.NV_ADDRESS, raw)
        check(f'nv garbage {raw.hex()} reads OFF', trial.call(fn['dsq_nv_read']) == 0)
    select(0)

    # 1. Fixture fidelity: stock reproduces the camera log, trial OFF is stock.
    for name, area, viewport, view, control, camera in CAMERA:
        ret = LCD if viewport == (720, 480) else HDMI
        got = fit(native, ret, area, viewport, view, control)
        check(f'stock reproduces camera {name}', got == camera, (got, camera))
        check(f'trial OFF identical {name}', fit(trial, ret, area, viewport, view, control) == got)

    # 2. Each factor: LCD live view letterboxed; other callers stay stock.
    results = []
    for index, (num, den) in enumerate(du.FACTORS, 1):
        select(index)
        for name, area, viewport, view, control, camera in CAMERA[:2]:
            got = fit(trial, LCD, area, viewport, view, control)
            ideal = expected(view, image_wh[area], num, den)
            error = max(abs(g - i) for g, i in zip(got, ideal))
            w, h = got[2] - got[0], got[3] - got[1]
            results.append(dict(factor=du.NAMES[index], case=name, rect=got, wh=(w, h),
                                aspect=round(w / h, 4), ideal=[round(v, 1) for v in ideal]))
            check(f'{du.NAMES[index]} {name} letterboxed within 2px', error <= 2, (got, ideal))
            check(f'{du.NAMES[index]} {name} even and inside view',
                  all(v % 2 == 0 for v in got) and got[0] >= view[0] and got[1] >= view[1]
                  and got[2] <= view[2] and got[3] <= view[3], got)
            for other in (LCD_STILL, None):
                ret = other or STOP | 1
                want = fit(native, ret, area, viewport, view, control)
                check(f'{du.NAMES[index]} {name} caller {ret:#x} untouched',
                      fit(trial, ret, area, viewport, view, control) == want)
        for name, area, viewport, view, control, camera in CAMERA[3:]:
            for ret in (HDMI, HDMI_STILL):
                want = fit(native, ret, area, viewport, view, control)
                check(f'{du.NAMES[index]} {name} {ret:#x} untouched',
                      fit(trial, ret, area, viewport, view, control) == want)

    # 3. Corrupt NV bytes fall back to stock.
    name, area, viewport, view, control, camera = CAMERA[0]
    trial.u.mem_write(du.NV_ADDRESS, b'\x02\x00')
    check('corrupt NV is stock', fit(trial, LCD, area, viewport, view, control) == camera)

    # 4. Power-on: no extension state allocated yet; the hook must not need it.
    select(1)
    before_state = bytes(trial.u.mem_read(trial.state, 64))
    trial.u.mem_write(trial.state, bytes(64))
    got = fit(trial, LCD, area, viewport, view, control)
    check('applies without extension state', got == results[0]['rect'], (got, results[0]['rect']))
    trial.u.mem_write(trial.state, before_state)
    check('native class pair table unchanged', bytes(trial.u.mem_read(0x115A4D10, 20)) ==
          bytes(native.u.mem_read(0x115A4D10, 20)))
    select(0)

    # 5. Immediate apply: change-mask builder wrapper.
    sw = lambda off: int.from_bytes(trial.u.mem_read(trial.state + info['state_offset'] + off, 4), 'little')
    sput = lambda off, v: trial.u.mem_write(trial.state + info['state_offset'] + off, struct.pack('<I', v))
    sput(du.S_MAGIC, 0)
    trial.call(fn['bitrate_restore'])
    check('restore initializes relayout state', sw(du.S_MAGIC) == du.STATE_MAGIC and
          sw(du.S_PENDING) == 0 and sw(du.S_FORCED) == 0)
    sput(du.S_PENDING, 1)
    trial.call(fn['bitrate_restore'])
    check('restore keeps a pending relayout', sw(du.S_PENDING) == 1)

    def stub_property(u, address, size, data):  # 0x10188776: native property read (not modelled).
        u.reg_write(UC_ARM_REG_R0, 0)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
    for ctx in (trial, native):
        ctx.u.hook_add(UC_HOOK_CODE, stub_property, begin=0x10188776, end=0x10188776)

    def mask(ctx, mode_changed):
        event, out, mptr = 0x50090000, 0x50090200, 0x50090300
        data = bytearray(0x100)
        for flag, value in ((0x5C, 3), (0x7C, 1), (0x6C, 2), (0x74, 0x1234), (0x84, 1)):
            struct.pack_into('<II', data, flag, 1, value)
        struct.pack_into('<II', data, 0x64, int(mode_changed), 4)
        ctx.u.mem_write(event, bytes(data))
        ctx.u.mem_write(out, b'\xa5' * 0x20)
        ctx.u.mem_write(mptr, struct.pack('<I', 0x10))
        r = ctx.call(du.MASK_BUILDER, (event, mptr, out))
        return r, int.from_bytes(ctx.u.mem_read(mptr, 4), 'little'), bytes(ctx.u.mem_read(out, 0x20))

    for changed in (False, True):
        sput(du.S_PENDING, 0)
        check(f'mask builder identical, mode_changed={changed}', mask(trial, changed) == mask(native, changed))
    sput(du.S_PENDING, 1)
    forced = sw(du.S_FORCED)
    got, want = mask(trial, False), mask(native, False)
    check('pending forces relayout bit once', got[1] == want[1] | du.MASK_RELAYOUT and got[0] == want[0]
          and got[2] == want[2] and sw(du.S_PENDING) == 0 and sw(du.S_FORCED) == forced + 1, (got[1], want[1]))
    check('relayout not repeated', mask(trial, False) == mask(native, False))
    sput(du.S_MAGIC, 0)
    sput(du.S_PENDING, 1)
    check('uninitialized state forces nothing', mask(trial, False) == mask(native, False) and sw(du.S_PENDING) == 1)
    sput(du.S_MAGIC, du.STATE_MAGIC)
    sput(du.S_PENDING, 0)

    # 6. Capture preview getters: only the capture callers see desqueezed sizes.
    from unicorn import UC_PROT_READ, UC_PROT_EXEC
    from unicorn.arm_const import UC_ARM_REG_SP
    from native_runtime import STACK
    from z30_extensions.features.aspect_ui import PREVIEW_CALLERS
    preview = info['preview']
    for ctx, binary in ((trial, image), (native, stock)):  # Firmware tables must stay read-only.
        ctx.u.mem_protect(0x10020000, (len(binary) + 4095) & ~4095, UC_PROT_READ | UC_PROT_EXEC)

    def at(ctx, target, lr, args):
        u = ctx.u
        for reg, value in zip((UC_ARM_REG_R0,), args):
            u.reg_write(reg, value)
        u.reg_write(UC_ARM_REG_SP, STACK + 0xF0000)
        u.reg_write(UC_ARM_REG_LR, lr)
        stop = u.hook_add(UC_HOOK_CODE, lambda uc, addr, n, d: uc.emu_stop(), begin=lr & ~1, end=lr & ~1)
        try:
            u.emu_start(target | 1, lr & ~1, count=10000)
        finally:
            u.hook_del(stop)
        assert u.reg_read(UC_ARM_REG_PC) == lr & ~1 and u.reg_read(UC_ARM_REG_SP) == STACK + 0xF0000
        pointer = u.reg_read(UC_ARM_REG_R0)
        return pointer, struct.unpack('<2H', u.mem_read(pointer, 4))

    getter_cases = 0
    for kind, getter, stock_pairs in (('small', du.PREVIEW_SMALL[0], du.STOCK_SMALL),
                                      ('large', du.PREVIEW_LARGE[0], du.STOCK_LARGE)):
        for factor in range(len(du.NAMES)):
            select(factor)
            for lr in PREVIEW_CALLERS[kind] + (0x1018AE6F, 0x1038FAD3, 0x10221B59):
                for index in range(5):
                    got, want = at(trial, getter, lr, (index,)), at(native, getter, lr, (index,))
                    capture = lr in PREVIEW_CALLERS[kind] and factor
                    wanted_pair = tuple(preview[kind][factor - 1][index]) if capture else stock_pairs[index]
                    check(f'{kind} getter f{factor} lr {lr:#x} i{index}', got[1] == wanted_pair and
                          (capture or got == want), (got, want, wanted_pair))
                    getter_cases += 1
    select(0)

    # 7. Complete native preview buffer producers (aspect-profiles harness shape).
    obj, record, holder = 0x50080000, 0x50081000, 0x50088000
    producers = ((0x10142D86, (0x1a, 0x3a)), (0x1014376E, (0x22,)), (0x101464C4, (0x1a, 0x36)))

    def produce(ctx, address, index):
        ctx.u.mem_write(obj, bytes(0x100))
        ctx.u.mem_write(record, bytes(0x6000))
        ctx.u.mem_write(obj + 4, struct.pack('<I', holder))
        ctx.u.mem_write(holder, struct.pack('<I', 0x50090000))
        for delta in (0x226, 0x4e8):
            ctx.u.mem_write(record + delta, bytes([index]))
        ctx.call(address, (obj, record))
        return bytes(ctx.u.mem_read(obj, 0x100))

    producer_cases = []
    for address, offsets in producers:
        stock_out = {i: produce(native, address, i) for i in range(5)}
        for factor in range(len(du.NAMES)):
            select(factor)
            for index in range(5):
                out = produce(trial, address, index)
                if not factor:
                    check(f'producer {address:#x} OFF i{index} stock', out == stock_out[index])
                    continue
                pairs = [struct.unpack_from('<2H', out, o) for o in offsets]
                wanted = [tuple(preview['small'][factor - 1][index]), tuple(preview['large'][factor - 1][index])]
                if len(offsets) == 1:
                    wanted = wanted[1:]
                check(f'producer {address:#x} f{factor} i{index} sizes', pairs == wanted, (pairs, wanted))
                if len(offsets) == 2:
                    check(f'producer {address:#x} f{factor} i{index} strides',
                          out[0x16:0x1a] == stock_out[index][0x16:0x1a])
                # Allowed: preview heights, their aligned buffer heights (both only
                # shrink) and buffer addresses carved sequentially from the
                # holder base (only move lower). Widths and strides stay stock.
                bad = []
                for o in range(0, 0x100, 4):
                    new, old = out[o:o + 4], stock_out[index][o:o + 4]
                    if new == old:
                        continue
                    nw, ow = struct.unpack('<I', new)[0], struct.unpack('<I', old)[0]
                    nl, ol = struct.unpack('<2H', new), struct.unpack('<2H', old)
                    pointer = 0x50090000 <= nw <= ow < 0x51000000
                    height = nl[0] == ol[0] and nl[1] < ol[1] or nl[1] == ol[1] and nl[0] < ol[0]
                    if not (pointer or height):
                        bad.append((hex(o), old.hex(), new.hex()))
                check(f'producer {address:#x} f{factor} i{index} only heights/buffers shrink', not bad, bad)
                producer_cases.append(dict(producer=hex(address), factor=factor, index=index, wh=pairs))
    select(0)

    # 8. NEF playback: per-file embedded preview sizes for stock photo shapes.
    import verify_aspect_raw_playback
    from verify_aspect_raw_playback import verify as verify_raw
    # The shared harness defaults to the aspect XPAN file shape; use stock DX.
    verify_aspect_raw_playback.Fixture.prepare.__defaults__ = (tuple(du.PHOTO_SHAPES[0][0]), 1, 0)
    raw = verify_raw(stock, image, {'features': {'aspect_profiles': {'raw_preview_playback':
                     preview['raw_preview_playback']}}})
    # JPEG (type 0) and RAW+JPEG: same native producers with a type-0 file.
    from verify_aspect_raw_playback import Fixture as RawFixture, OBJ, DESC, INFO

    class JpegFixture(RawFixture):
        def service(self, u, address, size, data):
            if address == 0x103A1FA8:
                r = [u.reg_read(reg) for reg in (UC_ARM_REG_R0, UC_ARM_REG_R1)]
                assert r[0] in (4, 5) and r[1] in du.PLAYBACK_FILE_TYPES, r
                self.queries.append(r)
                u.mem_write(u.reg_read(UC_ARM_REG_R3), struct.pack('<2H', *self.preview[r[0]]))
                u.reg_write(UC_ARM_REG_R0, 0)
                u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
                return
            super().service(u, address, size, data)

    jpeg_trial, jpeg_native = JpegFixture(stock, image), JpegFixture(stock, stock)
    jpeg_cases = []
    previews = {4: tuple(preview['small'][0][0]), 5: tuple(preview['large'][0][0])}
    jpeg_shapes = sorted({tuple(wh) for k in info['jpeg']['shapes'].values() for a in k.values() for wh in a})
    for wh in [list(s) for area in du.PHOTO_SHAPES for s in area] + [list(w) for w in jpeg_shapes] + [[1234, 567]]:
        target = any(tuple(wh) in area for area in du.PHOTO_SHAPES) or tuple(wh) in jpeg_shapes
        for file_type in (0, 4):
            outs = []
            for fx in (jpeg_native, jpeg_trial):
                fx.prepare(wh, file_type, 0)
                fx.preview = previews
                fx.call(0x103472DE, (OBJ,))
                outs.append(bytes(fx.u.mem_read(DESC, 0x90)))
            if target and file_type == 0:
                seen = {}
                for i in range(6):
                    kind, enabled, w, h = struct.unpack_from('<B3x3I', outs[1], i * 24)
                    if enabled == 1 and kind in (4, 5):
                        seen[kind] = (w, h)
                check(f'JPEG {wh} descriptor uses file previews', seen and all(seen[k] == previews[k] for k in seen)
                      and jpeg_trial.queries, (seen, previews))
            else:
                check(f'type {file_type} {wh} descriptor stock', outs[0] == outs[1] and not jpeg_trial.queries)
            jpeg_cases.append(dict(wh=wh, file_type=file_type, changed=outs[0] != outs[1]))
    check('stock JPEG path reproduces the enum-size mismatch', any(c['changed'] for c in jpeg_cases))
    for kind, stream in ((4, 7), (5, 2)):
        jpeg_trial.prepare((5568, 3712), 0, 0)
        jpeg_trial.preview = previews
        jpeg_trial.u.mem_write(OBJ + 6, struct.pack('<H', stream))
        jpeg_trial.call(0x10340C84, (OBJ,))
        got = struct.unpack('<2H', jpeg_trial.u.mem_read(INFO + 0x39c, 4))
        check(f'JPEG size preparation kind {kind}', got == previews[kind], got)

    check('NEF preview playback uses file sizes', raw['native_producer_cases'] and raw['metadata_cases'],
          len(raw['native_producer_cases']))

    # 9. NV JPEG flag (bit 4 of byte 0) and backward compatibility.
    def raw_nv(b0):
        trial.u.mem_write(du.NV_ADDRESS, bytes([b0, b0 ^ 0xFF]))
    raw_nv(0x01)
    check('v5 value reads factor 1, JPEG off', trial.call(fn['dsq_nv_read']) == 1 and trial.call(fn['dsq_nv_jpeg']) == 0)
    trial.call(fn['dsq_nv_toggle_jpeg'])
    check('toggle sets JPEG flag, keeps factor', nv() == bytes([0x11, 0xEE]) and trial.call(fn['dsq_nv_read']) == 1
          and trial.call(fn['dsq_nv_jpeg']) == du.NV_JPEG)
    trial.call(fn['dsq_nv_write'], (3,))
    check('factor write keeps JPEG flag', nv() == bytes([0x13, 0xEC]))
    trial.call(fn['dsq_nv_write'], (0,))
    check('OFF keeps JPEG flag', nv() == bytes([0x10, 0xEF]) and trial.call(fn['dsq_nv_read']) == 0)
    trial.call(fn['dsq_nv_toggle_jpeg'])
    check('toggle back to factory zero', nv() == bytes(2))
    for b0 in (0x15, 0x21, 0x81, 0x17):
        raw_nv(b0)
        check(f'invalid byte {b0:#x} reads OFF', trial.call(fn['dsq_nv_read']) == 0 and trial.call(fn['dsq_nv_jpeg']) == 0)
    select(0)

    # 10. Output-size getter: only capture callers, JPEG flag + factor, areas 0/2/4, sizes 0-2.
    shapes = {int(k): {int(a): v for a, v in areas.items()} for k, areas in info['jpeg']['shapes'].items()}
    getter_jpeg = 0
    others = (0x1018AE57, 0x10221B45, 0x1034831F, 0x101D5C3D)
    for flag in (0, 1):
        for factor in range(len(du.NAMES)):
            raw_nv(factor | (du.NV_JPEG if flag else 0)) if (factor or flag) else select(0)
            for lr in du.CAPTURE_OUTPUT_CALLERS + others:
                for area in (0, 1, 2, 4):
                    for size in range(4):
                        u = trial.u
                        u.reg_write(UC_ARM_REG_R1, size)
                        got = at(trial, du.OUTPUT_GETTER, lr, (area,))
                        native.u.reg_write(UC_ARM_REG_R1, size)
                        want = at(native, du.OUTPUT_GETTER, lr, (area,))
                        active = (flag and factor and lr in du.CAPTURE_OUTPUT_CALLERS
                                  and area in du.JPEG_AREAS and size <= 2)
                        if active:
                            wanted = tuple(shapes[factor][area][size])
                            check(f'output f{factor} lr {lr:#x} a{area} s{size}', got[1] == wanted, (got, wanted))
                        else:
                            check(f'output stock f{factor} j{flag} lr {lr:#x} a{area} s{size}', got == want, (got, want))
                        getter_jpeg += 1
    select(0)

    # 11. Complete capture-stage builders with the JPEG flag (synthetic capture info).
    obj2, rec2, dims2, holder2 = 0x500A0000, 0x500A1000, 0x500A8000, 0x500A9000

    def stage(ctx, address, size, area=0):
        ctx.u.mem_write(obj2, bytes(0x200))
        ctx.u.mem_write(rec2, bytes(0x3000))
        ctx.u.mem_write(rec2 + 0x208, bytes([area]))
        ctx.u.mem_write(rec2 + 0x229, bytes([size]))
        ctx.u.mem_write(obj2 + 4, struct.pack('<I', holder2))
        ctx.u.mem_write(holder2, struct.pack('<I', 0x500B0000))
        ctx.u.mem_write(dims2, struct.pack('<2H', 6000, 4000))
        ctx.call(address, (obj2, rec2, dims2, 0))
        return bytes(ctx.u.mem_read(obj2, 0x200))

    stage_cases = []
    # Output W/H fields per builder (from the camera capture-probe log).
    fields = {0x1014237A: (0x56, 0x58), 0x1014319E: (0x42, 0x44)}
    for address, (fw, fh) in fields.items():
        for area in (0, 2):
            for size in range(3):
                select(0)
                stock_out = stage(native, address, size, area)
                check(f'stage {address:#x} a{area} s{size} OFF identical', stage(trial, address, size, area) == stock_out)
                raw_nv(1)  # factor without the JPEG flag: main JPEG untouched
                off = stage(trial, address, size, area)
                # 0x1014319E also carries the (v4/v5) desqueezed preview sizes.
                check(f'stage {address:#x} a{area} s{size} flag off: main size stock',
                      off[fw:fw + 2] + off[fh:fh + 2] == stock_out[fw:fw + 2] + stock_out[fh:fh + 2] and
                      (address != 0x1014237A or off == stock_out))
                for factor in range(1, len(du.NAMES)):
                    raw_nv(factor | du.NV_JPEG)
                    out = stage(trial, address, size, area)
                    w, h = struct.unpack_from('<H', out, fw)[0], struct.unpack_from('<H', out, fh)[0]
                    sw_, sh_ = struct.unpack_from('<H', stock_out, fw)[0], struct.unpack_from('<H', stock_out, fh)[0]
                    # The stage's own size index may come from other capture-info
                    # fields; expect the desqueezed form of whatever stock produced.
                    wanted = du.desqueezed16([(sw_, sh_)], du.FACTORS[factor - 1])[0]
                    check(f'stage {address:#x} f{factor} a{area} s{size} size', (w, h) == wanted, ((w, h), wanted))
                    check(f'stage {address:#x} f{factor} a{area} s{size} width stock', w == sw_)
                    if address == 0x1014237A:  # aligned output buffer still covers the image
                        bw, bh = struct.unpack_from('<2H', out, 0x52)
                        obw, obh = struct.unpack_from('<2H', stock_out, 0x52)
                        check(f'stage {address:#x} f{factor} a{area} s{size} buffer', bw == obw and h <= bh <= obh,
                              (bw, bh, obw, obh))
                    else:  # encoder's compressed-buffer size: per size index, never larger than stock
                        est, oest = struct.unpack_from('<I', out, 0x3C)[0], struct.unpack_from('<I', stock_out, 0x3C)[0]
                        check(f'stage {address:#x} f{factor} a{area} s{size} JPEG buffer', 0 < est <= oest, (est, oest))
                    stage_cases.append(dict(stage=hex(address), area=area, size=size, factor=factor, wh=[w, h]))
    select(0)

    summary = dict(eg_sha256=report['sha256'], size=report['size'],
                   edits=[(e['name'], e['offset']) for e in report['edits']],
                   passed=sum(1 for c in checks if c['ok']), results=results, preview_getter_cases=getter_cases,
                   preview_producer_cases=producer_cases, output_getter_cases=getter_jpeg,
                   jpeg_stage_cases=stage_cases, raw_playback_cases=len(raw['native_producer_cases']),
                   checks=checks)
    print(f"{summary['passed']} checks passed; EG sha256 {report['sha256']}")
    for r in results:
        print(f"  {r['factor']:6} {r['case']:10} {str(r['rect']):22} {r['wh'][0]}x{r['wh'][1]}  {r['aspect']}")
    if a.output:
        a.output.write_text(json.dumps(summary, indent=2, default=str) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
