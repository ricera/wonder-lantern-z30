"""Experimental anamorphic desqueeze for live view, photo previews and JPEGs (v6).

Camera probe (docs/desqueeze-probe.md, WL-DSQ.DBG of 2026-11-01) showed the
stock display fit 0x1018ADF0 runs only on live-view setup, from the 0x1018AF6C
cluster. Its first LCD call (return 0x1018AFDB) fits the current mode's image
(3:2 photo, 16:9 movie) into the LCD container; the other calls fit the
still-image size or the HDMI viewport.

For that one caller, the width of the class->display-size pair returned by
0x1020C038 at 0x1018AE6A is multiplied by the selected squeeze factor, so the
fit letterboxes a wider picture. The native pair is never written; only the
fit's local copy is. The fit's ratio class (r8) is made unmatched so its
same-class edge snap cannot restore full height. No sensor, recording,
playback, HDMI or still-image geometry is changed.

v2 storage (docs/desqueeze-persistence.md): the factor lives in two padding
bytes of native persistent settings group 0x01 (NV_ADDRESS), which no property
key covers and which the native settings journal saves and restores with the
group's block. Byte 0 = factor index, byte 1 = index ^ 0xFF; anything else
(including the zero factory default) is OFF. The display hook reads these
bytes directly, so the factor applies from the first live-view setup after
power-on, without the Extensions menu or SD access. A changed factor applies at
the next live-view setup.

v3 immediate apply: the display update 0x1018CA30 (display task) only lays
out live view when its change mask, built by 0x10197F42 (single caller,
return 0x1018CA8F), is non-zero. Bit 3 is what the firmware sets when the
live-view mode property 0x1824 changes; it drives the 0x1018AF6C layout, the
source window 0x10197D78 and the output apply. A menu change of the factor
sets a pending flag in extension state; the next display update ORs bit 3
into the mask and clears the flag, so the new factor is laid out without a
photo <-> movie switch. At boot no extension state exists and nothing is
forced (the first native layout already reads the stored factor).

v4 desqueezed photo previews: while a factor is stored, the two JPEG previews
generated at capture (small 640-class and large 1620/1920-class, embedded in
the file and shown by normal single-frame playback) keep their stock width
and get their height divided by the factor, so the full frame is scaled
non-proportionally to the desqueezed aspect. Only the capture consumers found
by the aspect-profiles work see the new sizes: the two preview getters
0x1020C02E / 0x1020C038 for PREVIEW_CALLERS, and the five direct buffer
producers reading 0x1157223C. Row strides (2 x width) are unchanged. The main
image, thumbnails and existing files are untouched. NEF playback otherwise
and JPEG playback configure preview buffers from the stock area enum (JPEG
whenever the main image is taller than 1080, i.e. every stock size); the
per-file preview query from aspect_raw_playback is installed for the stock
photo shapes and file types 0-2, so each JPEG/NEF uses its own embedded
preview dimensions, including both files of a RAW+JPEG pair.

v6 opt-in desqueezed main JPEG: capture probe v1 (docs/desqueeze-jpeg.md)
showed L, M and S share one resize stage (0x1014237A) with a fixed
5568x3712 input block, and the JPEG encoder setup (0x1014319E); both take the
output size and every derived buffer from the output getter 0x1020BFDA
(area +0x208, size +0x229). With "Desqueeze JPEG" on and a factor stored,
those capture callers get the stock width and height/factor (multiple of
16), so the resizer writes a desqueezed main image. The NEF raw data and all
other getter callers (live view, playback, file metadata) are unchanged.
NV byte 0 bit 4 holds the JPEG flag (bits 0-2 the factor index); old
values remain valid with the flag off.
"""
import struct
from ..assembly import constant as c, invoke as n, assemble, jump

VERSION = 6
PAGE_INDEX = 964  # Own Extensions category page (after zebra 963).
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = 125, 126, 127, 133, 139
NAMES = ('OFF (stock)', '1.33x', '1.5x', '1.8x', '2.0x')
JPEG_ROW = len(NAMES)
ROW_LABELS = NAMES + ('Desqueeze JPEG',)
NV_JPEG = 0x10  # Byte-0 flag; bits 0-2 hold the factor index.
FACTORS = ((4, 3), (3, 2), (9, 5), (2, 1))  # Width multipliers for indices 1..4.

SITE, SITE_LENGTH, SITE_CONTINUE = 0x1018AE6A, 10, 0x1018AE74
# bl 0x1020C038; ldrh r1,[r0]; strh.w r1,[sp,#4]
SITE_EXPECTED = bytes.fromhex('81f0e5f80188adf80410')
CLASS_SIZE = 0x1020C038
LCD_LIVEVIEW_CALLER = 0x1018AFDB  # 0x1018AF6C first call, LCD output.
FIT_SAVED_LR = 0x54  # Fit frame: push {r4-r8,lr}; sub sp,#0x40.
UNMATCHED_CLASS = 0xFF

# Native persistent settings storage (Z30 C 1.20).
GROUP_TABLE, VALUE_TABLE = 0x114F774C, 0x114EB97C
NV_GROUP = 0x01
NV_ADDRESS = 0xC25254EE  # Padding between key 0x0107 (u16 @..EC) and 0x0108 (u32 @..F0).
NV_GROUP_RAM, NV_GROUP_SIZE = 0xC25254D0, 0x80
BACKUP_LIST, BACKUP_COUNT = 0x120335E4, 0x6C  # Image copy of the runtime block list.

MASK_BUILDER, MASK_LENGTH, MASK_CONTINUE = 0x10197F42, 10, 0x10197F4C
# push.w {r4-r8,lr}; mov r5,r0; ldr r0,[r0,#0x5c]; movs r6,#0
MASK_EXPECTED = bytes.fromhex('2de9f0410546c06d0026')
MASK_RELAYOUT = 8  # Native bit for a live-view mode (0x1824) change.

# Extension state words. 0/4 are written by the framework (default, fd).
S_PENDING, S_MAGIC, S_FORCED = 8, 12, 16
STATE_MAGIC = 0x33515344  # DSQ3
STATE_BYTES = 20

# Stock preview tables: getters (small 0x115A4CFC, large 0x115A4D10) and the
# direct producer span 0x1157223C (small, small stride, large, large stride).
PREVIEW_SMALL, PREVIEW_LARGE = (0x1020C02E, 0x115A4CFC), (0x1020C038, 0x115A4D10)
STOCK_SMALL = ((640, 424), (640, 360), (600, 480), (640, 480), (480, 480))
STOCK_LARGE = ((1620, 1080), (1920, 1080), (1350, 1080), (1440, 1080), (1080, 1080))
# Stock photo shapes per image area (JPEG L, M, S). install() adds the padded
# RAW plane of each L size. Playback file types: 0 JPEG, 1/2 NEF (0x103A1FA8
# serves all three; 4/5 are movies).
STOCK_JPEG = (((5568, 3712), (4176, 2784), (2784, 1856)),     # DX 3:2
              ((5568, 3128), (4176, 2344), (2784, 1560)),     # 16:9
              ((3712, 3712), (2784, 2784), (1856, 1856)))     # 1:1
PHOTO_SHAPES = (((5568, 3712), (4176, 2784), (2784, 1856)),   # DX 3:2
                ((5568, 3128), (4176, 2344), (2784, 1560)),   # 16:9
                ((3712, 3712), (2784, 2784), (1856, 1856)))   # 1:1
PLAYBACK_FILE_TYPES = (0, 1, 2)
# Main-JPEG output getter and its still-capture stage callers (return
# addresses): resize stage 0x1014237A, output buffer 0x10142BEA, encoder
# setup 0x1014319E (three reads).
OUTPUT_GETTER, OUTPUT_TABLE_LITERAL = 0x1020BFDA, 0x1020C214
OUTPUT_EXPECTED = bytes.fromhex('8e4a143252f8200000eb8100')
CAPTURE_OUTPUT_CALLERS = (0x10142967, 0x10142C23, 0x101432DF, 0x1014339D, 0x101435FB)
JPEG_AREAS = (0, 2, 4)  # DX, 16:9, 1:1

SIZES = dict(desqueeze_get=96, desqueeze_labels=320, desqueeze_open=160, desqueeze_action=192,
             desqueeze_restore=64, dsq_nv_read=48, dsq_nv_write=48, dsq_scale=192,
             dsq_state=64, dsq_mask=128, dsq_mask_original=32,
             dsq_nv_raw=48, dsq_nv_jpeg=32, dsq_nv_store=48, dsq_nv_toggle_jpeg=48)


def desqueezed(pairs, factor):
    """Keep the width; divide the height by the factor, nearest multiple of four."""
    num, den = factor
    out = []
    for w, h in pairs:
        exact = h * den / num
        low = int(exact) // 4 * 4
        out.append((w, low + 4 if exact - low > low + 4 - exact else low))
    return tuple(out)


def desqueezed16(pairs, factor):
    """Main-JPEG sizes: keep the width; height/factor to the nearest multiple of 16."""
    num, den = factor
    out = []
    for w, h in pairs:
        exact = h * den / num
        low = int(exact) // 16 * 16
        out.append((w, low + 16 if exact - low > low + 16 - exact else low))
    return tuple(out)


def check_storage(image):
    """Re-derive at build time that NV_ADDRESS is uncovered, persisted padding."""
    words = struct.unpack_from('<7I', image.read(GROUP_TABLE + NV_GROUP * 28, 28))
    size, ram, defaults, span, _, bank, kind = words
    if (size, ram, bank, kind) != (NV_GROUP_SIZE, NV_GROUP_RAM, 0, 1):
        raise ValueError(f'Unexpected settings group 0x01 descriptor: {[hex(w) for w in words]}')
    first, count = span & 0xFFFF, span >> 16
    for i in range(count):
        _, length, address, _ = struct.unpack_from('<4I', image.read(VALUE_TABLE + (first + i) * 16, 16))
        if address < NV_ADDRESS + 2 and NV_ADDRESS < address + length:
            raise ValueError(f'Desqueeze NV bytes overlap key {(NV_GROUP << 8) | i:#x}')
    if image.read(defaults + NV_ADDRESS - ram, 2) != bytes(2):
        raise ValueError('Desqueeze NV bytes have a non-zero factory default')
    blocks = [struct.unpack_from('<3I', image.read(BACKUP_LIST + i * 12, 12)) for i in range(BACKUP_COUNT)]
    if not any(a <= NV_ADDRESS and NV_ADDRESS + 2 <= a + s for a, s, _ in blocks):
        raise ValueError('Desqueeze NV bytes are not in a persisted settings block')
    return dict(group=hex(NV_GROUP), group_ram=hex(ram), group_size=hex(size), keys=count,
                defaults=hex(defaults), blocks=len(blocks))


def prepare(plan, f, resources, allocation_size):
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    page_offset, state_offset = allocation_size, allocation_size + 44
    displaced = plan.image.read(SITE, SITE_LENGTH)
    if displaced != SITE_EXPECTED:
        raise ValueError(f'Unexpected display-fit class lookup window: {displaced.hex()}')
    if plan.image.read(MASK_BUILDER, MASK_LENGTH) != MASK_EXPECTED:
        raise ValueError('Unexpected live-view change-mask builder prologue')
    storage = check_storage(plan.image)
    strings = {k: resources.alloc(v) for k, v in
               dict(title='Desqueeze', empty='', mark='*').items()}
    labels = plan.alloc(struct.pack(f'<{len(ROW_LABELS)}I', *(resources.alloc(v) for v in ROW_LABELS)))
    factors = plan.alloc(b''.join(struct.pack('<2H', *pair) for pair in FACTORS))
    call = lambda name: n(f[name])
    last = len(NAMES) - 1
    codes = {}
    # Leaf, r0-r3 only. Returns the stored factor index, or 0 unless the
    # check byte matches and the index is in range.
    # Leaf, r0-r3. Validated raw byte 0 (index | JPEG flag), or 0.
    codes['dsq_nv_raw'] = f'''{c('r3',NV_ADDRESS)}ldrb r0,[r3];ldrb r1,[r3,#1];
        eor r2,r0,#0xff;cmp r1,r2;bne off;tst r0,#{0xFF & ~(NV_JPEG | 7)};bne off;
        and r1,r0,#7;cmp r1,#{last};bhi off;bx lr;
        off:movs r0,#0;bx lr;'''
    codes['dsq_nv_read'] = f'''push {{r4,lr}};{call('dsq_nv_raw')}and r0,r0,#7;pop {{r4,pc}};'''
    codes['dsq_nv_jpeg'] = f'''push {{r4,lr}};{call('dsq_nv_raw')}and r0,r0,#{NV_JPEG};pop {{r4,pc}};'''
    # r0 = raw byte 0 (index | flag); 0 restores the factory zero bytes.
    codes['dsq_nv_store'] = f'''{c('r3',NV_ADDRESS)}movs r1,#0;cbz r0,put;eor r1,r0,#0xff;
        put:orr r0,r0,r1,lsl #8;strh r0,[r3];dmb ish;bx lr;'''
    # r0 = index; keeps the JPEG flag. Out-of-range indices store OFF.
    codes['dsq_nv_write'] = f'''push {{r4,lr}};mov r4,r0;cmp r4,#{last};it hi;movhi r4,#0;
        {call('dsq_nv_raw')}and r0,r0,#{NV_JPEG};orr r0,r0,r4;{call('dsq_nv_store')}pop {{r4,pc}};'''
    codes['dsq_nv_toggle_jpeg'] = f'''push {{r4,lr}};{call('dsq_nv_raw')}eor r0,r0,#{NV_JPEG};
        {call('dsq_nv_store')}pop {{r4,pc}};'''
    codes['desqueeze_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r0,r1;done:pop {{r4,pc}};'''
    # Initialized state or 0. No allocation (get is safe from any task).
    codes['dsq_state'] = f'''push {{r4,lr}};{call('desqueeze_get')}cmp r0,#0;beq done;
        ldr r1,[r0,#{S_MAGIC}];{c('r2',STATE_MAGIC)}cmp r1,r2;it ne;movne r0,#0;
        done:pop {{r4,pc}};'''
    # Called by the framework on first menu entry / photo trigger. Idempotent,
    # so a pending relayout survives repeated restores. No SD settings.
    codes['desqueeze_restore'] = f'''push {{r4,lr}};{call('desqueeze_get')}cmp r0,#0;beq done;
        ldr r1,[r0,#{S_MAGIC}];{c('r2',STATE_MAGIC)}cmp r1,r2;beq done;
        movs r1,#0;str r1,[r0,#{S_PENDING}];str r1,[r0,#{S_FORCED}];dmb ish;str r2,[r0,#{S_MAGIC}];
        done:movs r0,#0;pop {{r4,pc}};'''
    codes['desqueeze_labels'] = f'''push {{r4-r6,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['title'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        {call('dsq_nv_read')}mov r4,r0;
        movs r5,#0;
        loop:{c('r0',labels)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}cmp r5,r4;bne put;{c('r0',strings['mark'])}
        put:add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#{len(NAMES)};bne loop;
        {c('r0',labels)}ldr r0,[r0,#{JPEG_ROW * 4}];movs r1,#{LABEL_SLOT + JPEG_ROW};movs r2,#0;{call('text')}
        {call('dsq_nv_jpeg')}mov r6,r0;{c('r0',strings['empty'])}cbz r6,jpeg_put;{c('r0',strings['mark'])}
        jpeg_put:movs r1,#{MARK_SLOT + JPEG_ROW};movs r2,#0;{call('text')}
        {c('r0',labels)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4-r6,pc}};'''
    codes['desqueeze_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;
        movs r0,#2;{c('r1',PAGE_INDEX)}{n(0x1045f732)}
        {call('dsq_nv_read')}mov r4,r0;
        {call('get')}{c('r1',page_offset)}add r0,r0,r1;
        DSQ_IDS;ldr r1,[r1,r4,lsl #2];{n(0x1045d54c)}
        done:movs r0,#0;pop {{r4,pc}};'''
    codes['desqueeze_action'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r5,r0;cmp r5,#{JPEG_ROW};bhi done;bne factor;
        {call('dsq_nv_toggle_jpeg')}b root;
        factor:{call('dsq_nv_read')}mov r6,r0;
        mov r0,r5;{call('dsq_nv_write')}
        cmp r5,r6;beq root;{call('desqueeze_restore')}{call('dsq_state')}cbz r0,root;
        movs r1,#1;dmb ish;str r1,[r0,#{S_PENDING}];
        root:movs r0,#2;movw r1,#955;{n(0x1045f732)}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    # Entered by an absolute jump that displaced the original
    # "bl 0x1020C038; ldrh r1,[r0]; strh.w r1,[sp,#4]" in the fit.
    # r0 = ratio class; r4 = fit argument; r5 = 0; r8 = class (compared later).
    # The fit's own frame starts at sp; its caller return is at [sp+0x54].
    # Reads only the native pair and the NV bytes: no extension state,
    # allocation, file I/O or waits, so it is valid from the first boot layout.
    codes['dsq_scale'] = f'''push {{r4-r6,lr}};
        {n(CLASS_SIZE)}mov r5,r0;ldrh r6,[r5];
        ldr r1,[sp,#{16 + FIT_SAVED_LR}];{c('r2',LCD_LIVEVIEW_CALLER)}cmp r1,r2;bne out;
        {call('dsq_nv_read')}subs r1,r0,#1;cmp r1,#{len(FACTORS) - 1};bhi out;
        {c('r2',factors)}add r2,r2,r1,lsl #2;ldrh r3,[r2];ldrh r2,[r2,#2];
        sxth r0,r6;cmp r0,#0;ble out;mul r0,r0,r3;udiv r0,r0,r2;
        {c('r3',0x7FFF)}cmp r0,r3;bhi out;
        mov r6,r0;mov r8,#{UNMATCHED_CLASS};
        out:mov r0,r5;mov r1,r6;pop {{r4-r6,lr}};
        strh.w r1,[sp,#4];
        {c('r12',SITE_CONTINUE | 1)}bx r12;'''
    sizes = dict(SIZES)
    preview = install_preview(plan, f, codes, sizes, call, last)
    jpeg = install_jpeg(plan, f, codes, sizes, call)
    # Change-mask builder wrapper: run the original completely, then add the
    # relayout bit once per pending factor change.
    codes['dsq_mask_original'] = f'''push.w {{r4-r8,lr}};mov r5,r0;ldr r0,[r0,#0x5c];movs r6,#0;
        {c('r12',MASK_CONTINUE | 1)}bx r12;'''
    codes['dsq_mask'] = f'''push {{r4-r6,lr}};mov r4,r1;
        {call('dsq_mask_original')}mov r5,r0;
        {call('dsq_state')}cbz r0,out;
        ldr r1,[r0,#{S_PENDING}];cbz r1,out;movs r1,#0;str r1,[r0,#{S_PENDING}];
        ldr r1,[r0,#{S_FORCED}];adds r1,#1;str r1,[r0,#{S_FORCED}];
        ldr r1,[r4];orr r1,r1,#{MASK_RELAYOUT};str r1,[r4];
        out:mov r0,r5;pop {{r4-r6,pc}};'''
    plan.edit(MASK_BUILDER, jump(MASK_BUILDER, f['dsq_mask'], MASK_LENGTH),
              'Desqueeze: relayout live view after a factor change', MASK_EXPECTED)
    plan.edit(SITE, jump(SITE, f['dsq_scale'], SITE_LENGTH),
              'Desqueeze: LCD live-view display width factor', displaced)
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset + STATE_BYTES, default=0, codes=codes,
                displaced=displaced.hex(), storage=storage, sizes=sizes, preview=preview, jpeg=jpeg)


def install_jpeg(plan, f, codes, sizes, call):
    image = plan.image
    if image.read(OUTPUT_GETTER, len(OUTPUT_EXPECTED)) != OUTPUT_EXPECTED:
        raise ValueError('Unexpected output-size getter')
    table = struct.unpack('<I', image.read(OUTPUT_TABLE_LITERAL, 4))[0] + 0x14
    runtime = lambda a: a - 0xC1800000 + 0x11F81C94 if 0xC1800000 <= a < 0xC1900000 else a
    rows = {area: struct.unpack('<I', image.read(runtime(table) + area * 4, 4))[0] for area in JPEG_AREAS}
    stock = {area: tuple(struct.unpack('<2H', image.read(rows[area] + i * 4, 4)) for i in range(3))
             for area in JPEG_AREAS}
    expected = {0: STOCK_JPEG[0], 2: STOCK_JPEG[1], 4: STOCK_JPEG[2]}
    if stock != expected:
        raise ValueError(f'Unexpected stock JPEG output sizes: {stock}')
    # [factor][area 0..5][size 0..3] pairs; unused cells hold zero.
    data = bytearray(len(FACTORS) * 6 * 4 * 4)
    shapes = {}
    for k, factor in enumerate(FACTORS):
        shapes[k + 1] = {}
        for area in JPEG_AREAS:
            pairs = desqueezed16(stock[area], factor)
            shapes[k + 1][area] = pairs
            for i, wh in enumerate(pairs):
                struct.pack_into('<2H', data, ((k * 6 + area) * 4 + i) * 4, *wh)
    pairs_table = plan.alloc(bytes(data))
    callers = plan.alloc(struct.pack(f'<{len(CAPTURE_OUTPUT_CALLERS)}I', *CAPTURE_OUTPUT_CALLERS))
    area_mask = sum(1 << a for a in JPEG_AREAS)
    sizes['dsq_output'] = 192
    f['dsq_output'] = plan.alloc(bytes(192))
    # Replaces the stock getter: rows[area] + size * 4.
    codes['dsq_output'] = f'''push {{r4-r6,lr}};mov r4,r0;mov r5,r1;mov r6,lr;
        {c('r2',table)}ldr r2,[r2,r4,lsl #2];add r0,r2,r5,lsl #2;push {{r0}};
        cmp r4,#5;bhi original;cmp r5,#2;bhi original;
        movs r2,#1;lsl r2,r2,r4;tst r2,#{area_mask};beq original;
        {c('r1',callers)}movs r2,#{len(CAPTURE_OUTPUT_CALLERS)};
        next_caller:ldr r3,[r1],#4;cmp r6,r3;beq allowed;subs r2,#1;bne next_caller;b original;
        allowed:{call('dsq_nv_jpeg')}cbz r0,original;{call('dsq_nv_read')}subs r0,#1;cmp r0,#{len(FACTORS) - 1};bhi original;
        movs r1,#6;mul r0,r0,r1;add r0,r0,r4;lsls r0,r0,#2;add r0,r0,r5;
        {c('r1',pairs_table)}add r0,r1,r0,lsl #2;add sp,#4;pop {{r4-r6,pc}};
        original:pop {{r0}};pop {{r4-r6,pc}};'''
    plan.edit(OUTPUT_GETTER, jump(OUTPUT_GETTER, f['dsq_output'], 10), 'Desqueeze: capture JPEG output size')
    return dict(getter=hex(OUTPUT_GETTER), callers=[hex(v) for v in CAPTURE_OUTPUT_CALLERS],
                areas=list(JPEG_AREAS), nv_flag=hex(NV_JPEG),
                shapes={k: {a: [list(p) for p in v] for a, v in areas.items()} for k, areas in shapes.items()})


def install_preview(plan, f, codes, sizes, call, last):
    from .aspect_ui import PREVIEW_CALLERS, PREVIEW_DIRECT_SPAN, PREVIEW_DIRECT_HOOKS
    from . import aspect_raw_playback
    image = plan.image
    for (getter, table), stock in ((PREVIEW_SMALL, STOCK_SMALL), (PREVIEW_LARGE, STOCK_LARGE)):
        if struct.unpack_from('<10H', image.read(table, 20)) != tuple(v for wh in stock for v in wh):
            raise ValueError(f'Unexpected stock preview table {table:#x}')
    span_base, span_length = PREVIEW_DIRECT_SPAN
    span = image.read(span_base, span_length)
    if (struct.unpack_from('<10H', span, 0) != tuple(v for wh in STOCK_SMALL for v in wh) or
            struct.unpack_from('<10H', span, 40) != tuple(v for wh in STOCK_LARGE for v in wh)):
        raise ValueError('Unexpected direct preview producer span')
    small = [desqueezed(STOCK_SMALL, k) for k in FACTORS]
    large = [desqueezed(STOCK_LARGE, k) for k in FACTORS]
    pack = lambda pairs: b''.join(struct.pack('<2H', *wh) for wh in pairs)
    getter_tables = {}
    for kind, pairs in (('small', small), ('large', large)):
        getter_tables[kind] = plan.alloc(struct.pack(f'<{len(FACTORS)}I', *(plan.alloc(pack(p)) for p in pairs)))
    shadows = []
    for i in range(len(FACTORS)):
        data = bytearray(span)
        data[0:20], data[40:60] = pack(small[i]), pack(large[i])
        shadows.append(plan.alloc(bytes(data)))
    shadow_table = plan.alloc(struct.pack(f'<{len(FACTORS)}I', *shadows))
    count = len(FACTORS) - 1
    # Leaf on r0-r3: r0 = stored factor index - 1 if a factor is active, else -1.
    sizes['dsq_preview_factor'] = 32
    f['dsq_preview_factor'] = plan.alloc(bytes(32))
    codes['dsq_preview_factor'] = f'''push {{r4,lr}};{call('dsq_nv_read')}subs r0,#1;pop {{r4,pc}};'''
    for kind, (getter, table) in (('small', PREVIEW_SMALL), ('large', PREVIEW_LARGE)):
        name = 'dsq_preview_' + kind
        callers = PREVIEW_CALLERS[kind]
        caller_table = plan.alloc(struct.pack(f'<{len(callers)}I', *callers))
        sizes[name] = 192
        f[name] = plan.alloc(bytes(192))
        # Replaces the whole stock getter (base + index * 4).
        codes[name] = f'''push {{r4-r6,lr}};mov r4,r0;mov r5,lr;{c('r6',table)}add r6,r6,r4,lsl #2;
            cmp r4,#4;bhi original;
            {c('r1',caller_table)}movs r2,#{len(callers)};
            next_caller:ldr r3,[r1],#4;cmp r5,r3;beq allowed;subs r2,#1;bne next_caller;b original;
            allowed:{call('dsq_preview_factor')}cmp r0,#{count};bhi original;
            {c('r1',getter_tables[kind])}ldr r1,[r1,r0,lsl #2];add r0,r1,r4,lsl #2;pop {{r4-r6,pc}};
            original:mov r0,r6;pop {{r4-r6,pc}};'''
        plan.edit(getter, jump(getter, f[name], 8 if getter % 4 == 0 else 10),
                  'Desqueeze: capture preview size ' + kind)
    sizes['dsq_preview_data'] = 128
    f['dsq_preview_data'] = plan.alloc(bytes(128))
    codes['dsq_preview_data'] = f'''push {{r4-r6,lr}};mov r4,r0;
        {call('dsq_preview_factor')}cmp r0,#{count};bhi unchanged;mov r5,r0;
        {c('r0',span_base)}subs r6,r4,r0;cmp r6,#{span_length};bhs unchanged;
        {c('r0',shadow_table)}ldr r0,[r0,r5,lsl #2];add r0,r6;pop {{r4-r6,pc}};
        unchanged:mov r0,r4;pop {{r4-r6,pc}};'''
    for address, length, register, base, replay, expected in PREVIEW_DIRECT_HOOKS:
        name = f'dsq_preview_direct_{address:x}'
        sizes[name] = 192
        f[name] = plan.alloc(bytes(192))
        slot = {'r0': 0, 'r1': 4, 'r3': 12}[register]
        # Same register/lr-preserving shape as the camera-verified aspect hooks.
        codes[name] = f'''push {{r0-r3,r12,lr}};{c('r0',base)}
            {call('dsq_preview_data')}str r0,[sp,#{slot}];pop {{r0-r3,r12,lr}};
            {replay}push {{r0,r1}};{c('r0',(address+length)|1)}
            str r0,[sp,#4];pop {{r0,pc}};'''
        plan.edit(address, jump(address, f[name], length), 'Desqueeze: direct preview producer',
                  bytes.fromhex(expected))
    profiles = [dict(jpeg_wh=[list(wh) for wh in area]) for area in PHOTO_SHAPES]
    # Desqueezed main JPEGs (v6) have their own shapes; no RAW plane for them.
    extra = sorted({wh for factor in FACTORS for area in STOCK_JPEG for wh in desqueezed16(area, factor)})
    raw = aspect_raw_playback.install(plan, f, codes, sizes, profiles,
                                      file_types=PLAYBACK_FILE_TYPES, extra_shapes=extra)
    return dict(small=[list(map(list, p)) for p in small], large=[list(map(list, p)) for p in large],
                getters=[hex(PREVIEW_SMALL[0]), hex(PREVIEW_LARGE[0])],
                capture_callers={k: [hex(v) for v in vals] for k, vals in PREVIEW_CALLERS.items()},
                direct_hooks=[hex(h[0]) for h in PREVIEW_DIRECT_HOOKS], raw_preview_playback=raw)


def finish(plan, f, extension, identifiers):
    if len(identifiers) != len(ROW_LABELS):
        raise ValueError('Desqueeze page needs one control per factor')
    ids = plan.alloc(struct.pack(f'<{len(identifiers)}I', *identifiers))
    extension['codes']['desqueeze_open'] = extension['codes']['desqueeze_open'].replace('DSQ_IDS;', c('r1', ids))
    for name, source in extension['codes'].items():
        plan.put(f[name], assemble(source, f[name], extension['sizes'][name]))
    plan.features['desqueeze'] = dict(
        version=VERSION, names=list(NAMES), rows=list(ROW_LABELS), jpeg=extension['jpeg'], factors=[list(p) for p in FACTORS], default=0,
        page_index=PAGE_INDEX, page_offset=extension['page_offset'],
        state_offset=extension['state_offset'], allocation_size=extension['allocation_size'],
        functions={k: hex(f[k] | 1) for k in extension['sizes']}, controls=list(identifiers),
        preview=extension['preview'],
        site=hex(SITE), site_displaced=extension['displaced'], site_continue=hex(SITE_CONTINUE),
        class_size=hex(CLASS_SIZE), lcd_liveview_caller=hex(LCD_LIVEVIEW_CALLER),
        nv_address=hex(NV_ADDRESS), nv_encoding='byte0 = index, byte1 = index ^ 0xFF; else OFF',
        nv_storage=extension['storage'], settings_file=None,
        mask_builder=hex(MASK_BUILDER), mask_continue=hex(MASK_CONTINUE), mask_relayout=MASK_RELAYOUT,
        state_words=dict(pending=S_PENDING, magic=S_MAGIC, forced=S_FORCED), state_magic=hex(STATE_MAGIC),
        scope='LCD live-view display rectangle only; applied at each live-view setup, including the first after power-on',
        camera_verified=False,
        limitations=['A changed factor is laid out at the next live-view display update after the menu change (expected: menu exit).',
                     'Persistence relies on the native settings journal saving group 0x01 padding (static analysis).',
                     'Only the embedded JPEG previews are desqueezed; the main image, thumbnails, HDMI and recordings are unchanged.',
                     'Touch AF / focus-point mapping over the letterboxed picture is untested.'])
