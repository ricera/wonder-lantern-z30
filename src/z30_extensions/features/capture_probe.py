"""Opt-in observer of the still-capture geometry descriptors, for a desqueezed JPEG.

The main JPEG passes sensor -> internal crop (0x1020BFC8 sizes, e.g.
5600x3728) or ISP-reduced internal (0x1020C05C, 4200x2796) -> output
(0x1020BFDA, L/M/S 5568x3712 / 4176x2784 / 2784x1856). Six builders fill the
capture-stage descriptors from the capture info (area +0x208, size +0x229,
other indices +0x226..+0x22D, +0x239). This probe wraps each builder, runs
it completely, then records the builder, caller, 16 capture-info bytes and a
dump of the finished descriptor, so L/M/S shots show which stage resizes and
which crops. Observation only. The menu task writes A:\\WL-CAP.DBG.
"""
import struct
from ..assembly import assemble, constant as c, invoke as n, jump
from .sound_layout import SUCCESS

PAGE_INDEX = 960
TITLE_SLOT, ROOT_SLOT, LABEL_SLOT, MARK_SLOT, CURRENT_SLOT = 82, 83, 84, 87, 90
MAGIC = 0x31504357  # WCP1
VERSION = 1
DUMP = 0xD0
HEADER, CAPACITY, STRIDE = 80, 40, 12 + 7 * 4 + 0xD0
KEY_OFFSET, KEY_WORDS = 12, 7
STATE_BYTES = HEADER + CAPACITY * STRIDE
DUMP_OFFSET = 8  # Words 0/4 belong to the framework's selected/fd defaults.
DUMP_BYTES = STATE_BYTES - DUMP_OFFSET
MAX_PHASE = 9
LOG_NAME = 'WL-CAP.DBG'

# Capture-info bytes recorded per builder call (16, zero-padded).
INFO_OFFSETS = (0, 0x98, 0x208, 0x226, 0x227, 0x228, 0x229, 0x22A, 0x22B, 0x22C, 0x22D, 0x239)
# Builder entry, displaced length, expected bytes, replay of displaced code.
BUILDERS = (
    (0x10141B48, 10, '2de9f04104464ff00008', 'push.w {r4-r8,lr};mov r4,r0;mov.w r8,#0;'),
    (0x10141DC0, 10, '2de9f04104464ff00008', 'push.w {r4-r8,lr};mov r4,r0;mov.w r8,#0;'),
    (0x1014237A, 10, '2de9f84f002792460d46', 'push.w {r3-r11,lr};movs r7,#0;mov r10,r2;mov r5,r1;'),
    (0x10142BEA, 10, '2de9f04100250f461c21', 'push.w {r4-r8,lr};movs r5,#0;mov r7,r1;movs r1,#0x1c;'),
    (0x1014319E, 12, '2de9f84f4ff0000ac0f808a0', 'push.w {r3-r11,lr};mov.w r10,#0;str.w r10,[r0,#8];'),
    (0x10146F6A, 10, '2de9f041002646618661', 'push.w {r4-r8,lr};movs r6,#0;str r6,[r0,#0x14];str r6,[r0,#0x18];'),
)

# Header word offsets inside the probe state.
H_MAGIC, H_VERSION, H_PHASE, H_STATUS, H_SEQ, H_COUNT = 8, 12, 16, 20, 24, 28
H_LOCK, H_RESERVED, H_DROPPED, H_MASK, H_MASK_LR = 32, 36, 40, 44, 48
H_PENDING_ARG, H_PENDING_WH, H_PENDING_CLASS = 52, 56, 60
H_CAPACITY, H_STRIDE, H_HEADER = 64, 68, 72

STATUS = tuple(f'Phase {i}' for i in range(MAX_PHASE + 1)) + (
    'Log saved', 'Save failed', 'Camera busy', 'Log cleared')
S_SAVED, S_FAILED, S_BUSY, S_CLEARED = range(MAX_PHASE + 1, MAX_PHASE + 5)
LABELS = ('Next phase', 'Save log', 'Clear log')

SIZES = dict(bitrate_get=96, bitrate_labels=384, bitrate_open=192, bitrate_action=320,
             bitrate_restore=192, dsq_live=128, dsq_lock=96, dsq_unlock=32,
             dsq_phase=64, dsq_clear=192, dsq_save=512, dsq_record=1024,
             **{f'cap_original_{i}': 32 for i in range(6)},
             **{f'cap_wrapper_{i}': 64 for i in range(6)})


def _copy(label, field, destination, count):
    """Byte copy from the pointer at arg+field; source alignment is unknown."""
    return (f'ldr r1,[r4,#{field}];add r0,sp,#{destination};movs r2,#{count};'
            f'{label}:ldrb r3,[r1],#1;strb r3,[r0],#1;subs r2,#1;bne {label};')


def prepare(plan, f, resources, allocation_size):
    for name, size in SIZES.items():
        f[name] = plan.alloc(bytes(size))
    page_offset, state_offset = allocation_size, allocation_size + 44
    if state_offset % 4:
        raise ValueError('Capture probe state must be word aligned')
    strings = {k: resources.alloc(v) for k, v in
               dict(title='Capture probe', root='Capture probe', empty='').items()}
    labels = plan.alloc(struct.pack(f'<{len(LABELS)}I', *(resources.alloc(s) for s in LABELS)))
    statuses = plan.alloc(struct.pack(f'<{len(STATUS)}I', *(resources.alloc(s) for s in STATUS)))
    path = plan.alloc(('A:\\' + LOG_NAME).encode() + b'\0')
    call = lambda name: n(f[name])
    codes = {}
    codes['bitrate_get'] = f'''push {{r4,lr}};{call('get')}cmp r0,#0;beq done;
        {c('r1',state_offset)}add r0,r0,r1;done:pop {{r4,pc}};'''
    # Initialized state only; safe from any task (get is a leaf on r0-r2).
    codes['dsq_live'] = f'''push {{r4,lr}};{call('bitrate_get')}cmp r0,#0;beq done;
        ldr r1,[r0,#{H_MAGIC}];{c('r2',MAGIC)}cmp r1,r2;beq done;movs r0,#0;
        done:pop {{r4,pc}};'''
    # Idempotent: an initialized log survives repeated menu restores.
    codes['bitrate_restore'] = f'''push {{r4,lr}};{call('bitrate_get')}cmp r0,#0;beq done;mov r4,r0;
        ldr r1,[r4,#{H_MAGIC}];{c('r2',MAGIC)}cmp r1,r2;beq done;
        add r0,r4,#{DUMP_OFFSET};{c('r1',DUMP_BYTES)}{n(0x10199C40)}
        movs r0,#{VERSION};str r0,[r4,#{H_VERSION}];movs r0,#{CAPACITY};str r0,[r4,#{H_CAPACITY}];
        movs r0,#{STRIDE};str r0,[r4,#{H_STRIDE}];movs r0,#{HEADER};str r0,[r4,#{H_HEADER}];
        dmb ish;{c('r1',MAGIC)}str r1,[r4,#{H_MAGIC}];
        done:pop {{r4,pc}};'''
    codes['dsq_lock'] = f'''add r1,r0,#{H_LOCK};movs r2,#8;
        try:ldrex r3,[r1];cmp r3,#0;bne retry;movs r3,#1;strex r12,r3,[r1];cmp r12,#0;beq ok;
        retry:subs r2,#1;bne try;clrex;movs r0,#0;bx lr;
        ok:dmb ish;movs r0,#1;bx lr;'''
    codes['dsq_unlock'] = f'''dmb ish;movs r1,#0;str r1,[r0,#{H_LOCK}];bx lr;'''
    codes['dsq_phase'] = f'''ldr r1,[r0,#{H_PHASE}];cmp r1,#{MAX_PHASE};it lo;addlo r1,#1;
        str r1,[r0,#{H_PHASE}];str r1,[r0,#{H_STATUS}];bx lr;'''
    codes['dsq_clear'] = f'''push {{r4,lr}};mov r4,r0;{call('dsq_lock')}cmp r0,#1;bne busy;
        add r0,r4,#{HEADER};{c('r1',CAPACITY*STRIDE)}{n(0x10199C40)}
        movs r0,#0;str r0,[r4,#{H_PHASE}];str r0,[r4,#{H_SEQ}];str r0,[r4,#{H_COUNT}];
        str r0,[r4,#{H_DROPPED}];str r0,[r4,#{H_MASK}];str r0,[r4,#{H_MASK_LR}];
                movs r0,#{S_CLEARED};str r0,[r4,#{H_STATUS}];mov r0,r4;{call('dsq_unlock')}pop {{r4,pc}};
        busy:movs r0,#{S_BUSY};str r0,[r4,#{H_STATUS}];pop {{r4,pc}};'''
    # Menu task only. Holding the record lock during the write makes display
    # callbacks drop (and count) events instead of waiting.
    codes['dsq_save'] = f'''push {{r4-r8,lr}};sub sp,#64;mov r4,r0;
        {c('r0',0x188C)}{n(0x101993DE)}cmp r0,#0;bne busy;
        mov r0,r4;{call('dsq_lock')}cmp r0,#1;bne busy;
        mov r0,sp;movs r1,#48;{n(0x10199C40)}
        mvn r0,#0;str r0,[sp,#8];mov r0,sp;{c('r1',path)}{n(0x1033829E)}
        mov r0,sp;{c('r1',0x301)}movs r2,#0;{n(0x103382A2)}
        {c('r1',SUCCESS)}cmp r0,r1;bne failed;
        movs r0,#0;str r0,[sp,#48];ldr r0,[sp,#8];
        add r1,r4,#{DUMP_OFFSET};{c('r2',DUMP_BYTES)}add r3,sp,#48;{n(0x102C8A6A)}
        mov r5,r0;ldr r6,[sp,#48];
        mov r0,sp;{n(0x1033832E)}{c('r1',SUCCESS)}cmp r0,r1;bne failed;
        cmp r5,#0;bne failed;{c('r1',DUMP_BYTES)}cmp r6,r1;bne failed;
        movs r5,#{S_SAVED};b unlock;
        failed:movs r5,#{S_FAILED};
        unlock:mov r0,r4;{call('dsq_unlock')}str r5,[r4,#{H_STATUS}];b done;
        busy:movs r0,#{S_BUSY};str r0,[r4,#{H_STATUS}];
        done:add sp,#64;pop {{r4-r8,pc}};'''
    # r0 = descriptor object, r1 = capture info, r2 = caller, r3 = builder id.
    # Key (7 words at record+12): phase, builder id, caller, 16 info bytes.
    # The descriptor dump (not part of the key) follows the key.
    info_bytes = ''.join(f'ldrb r0,[r5,#{off}];strb r0,[sp,#{12 + i}];' for i, off in enumerate(INFO_OFFSETS))
    codes['dsq_record'] = f'''push {{r4-r11,lr}};sub sp,#{KEY_WORDS * 4 + 4};mov r4,r0;mov r5,r1;mov r8,r2;mov r9,r3;
        {call('dsq_live')}cmp r0,#0;beq done;mov r6,r0;
        add r1,r6,#{H_SEQ};movs r2,#4;
        seq_try:ldrex r7,[r1];adds r7,#1;strex r0,r7,[r1];cmp r0,#0;beq seq_ok;
        subs r2,#1;bne seq_try;clrex;b done;
        seq_ok:ldr r0,[r6,#{H_PHASE}];str r0,[sp];str r9,[sp,#4];str r8,[sp,#8];
        movs r0,#0;str r0,[sp,#12];str r0,[sp,#16];str r0,[sp,#20];str r0,[sp,#24];
        {info_bytes}
        mov r0,r6;{call('dsq_lock')}cmp r0,#1;bne drop;
        ldr r8,[r6,#{H_COUNT}];movs r9,#0;add r10,r6,#{HEADER};
        search:cmp r9,r8;bhs insert;
        ldr r0,[r10];cmp r0,#0;beq next_record;
        add r0,r10,#{KEY_OFFSET};mov r1,sp;movs r2,#{KEY_WORDS};
        compare:ldr r3,[r0],#4;ldr r11,[r1],#4;cmp r3,r11;bne next_record;subs r2,#1;bne compare;
        ldr r0,[r10,#4];adds r0,#1;str r0,[r10,#4];str r7,[r10,#8];b unlock;
        next_record:adds r9,#1;add r10,r10,#{STRIDE};b search;
        insert:cmp r8,#{CAPACITY};bhs full;
        add r0,r10,#{KEY_OFFSET};mov r1,sp;movs r2,#{KEY_WORDS};
        store:ldr r3,[r1],#4;str r3,[r0],#4;subs r2,#1;bne store;
        mov r1,r4;{c('r2',DUMP)}
        dump:ldrb r3,[r1],#1;strb r3,[r0],#1;subs r2,#1;bne dump;
        movs r0,#1;str r0,[r10,#4];str r7,[r10,#8];dmb ish;str r7,[r10];
        adds r8,#1;str r8,[r6,#{H_COUNT}];b unlock;
        full:ldr r0,[r6,#{H_DROPPED}];adds r0,#1;str r0,[r6,#{H_DROPPED}];
        unlock:mov r0,r6;{call('dsq_unlock')}b done;
        drop:ldr r0,[r6,#{H_DROPPED}];adds r0,#1;str r0,[r6,#{H_DROPPED}];
        done:add sp,#{KEY_WORDS * 4 + 4};pop {{r4-r11,pc}};'''
    for index, (address, length, expected, replay) in enumerate(BUILDERS):
        original, wrapper = f'cap_original_{index}', f'cap_wrapper_{index}'
        codes[original] = f'''{replay}{c('r12',(address + length) | 1)}bx r12;'''
        # Run the complete builder, then record (obj, info, caller, id).
        codes[wrapper] = f'''push {{r0-r3}};push {{r4,r5,r6,lr}};
            {call(original)}mov r4,r0;
            ldr r0,[sp,#16];ldr r1,[sp,#20];ldr r2,[sp,#12];movs r3,#{index};{call('dsq_record')}
            mov r0,r4;pop {{r4,r5,r6,lr}};add sp,#16;bx lr;'''
        plan.edit(address, jump(address, f[wrapper], length), f'Capture probe: observe builder {address:#x}',
                  bytes.fromhex(expected))
    codes['bitrate_labels'] = f'''push {{r4-r6,lr}};
        {c('r0',strings['title'])}movs r1,#{TITLE_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['root'])}movs r1,#{ROOT_SLOT};movs r2,#0;{call('text')}
        movs r5,#0;
    labels_loop:
        {c('r0',labels)}ldr r0,[r0,r5,lsl #2];add r1,r5,#{LABEL_SLOT};movs r2,#0;{call('text')}
        {c('r0',strings['empty'])}add r1,r5,#{MARK_SLOT};movs r2,#0;{call('text')}
        adds r5,#1;cmp r5,#{len(LABELS)};blo labels_loop;
        {call('bitrate_get')}movs r4,#0;cmp r0,#0;beq put_status;
        ldr r4,[r0,#{H_STATUS}];cmp r4,#{len(STATUS)-1};it hi;movhi r4,#0;
    put_status:
        {c('r0',statuses)}ldr r0,[r0,r4,lsl #2];movs r1,#{CURRENT_SLOT};movs r2,#0;{call('text')}
        movs r0,#0;pop {{r4-r6,pc}};'''
    codes['bitrate_open'] = f'''push {{r4,lr}};{call('ensure')}cmp r0,#0;beq done;
        movs r0,#2;movw r1,#{PAGE_INDEX};{n(0x1045F732)}
        {call('get')}{c('r1',page_offset)}add r0,r0,r1;
        DSQ_IDS;ldr r1,[r1];{n(0x1045D54C)}
        done:movs r0,#0;pop {{r4,pc}};'''
    codes['bitrate_action'] = f'''push {{r4-r6,lr}};mov r4,r0;
        ldrh r5,[r4,#22];ldrh r0,[r4,#24];add r5,r0;cmp r5,#{len(LABELS)-1};bhi done;
        {call('dsq_live')}cmp r0,#0;beq done;mov r6,r0;
        cmp r5,#0;bne not_phase;{call('dsq_phase')}b refresh;
        not_phase:cmp r5,#1;bne clear;{call('dsq_save')}b refresh;
        clear:{call('dsq_clear')}
        refresh:{call('bitrate_labels')}movs r0,#2;movw r1,#955;{n(0x1045F732)}
        done:movs r0,#0;pop {{r4-r6,pc}};'''
    return dict(page_offset=page_offset, state_offset=state_offset,
                allocation_size=state_offset + STATE_BYTES, default=0, codes=codes)


def finish(plan, f, extension, identifiers):
    ids = plan.alloc(struct.pack(f'<{len(identifiers)}I', *identifiers))
    extension['codes']['bitrate_open'] = extension['codes']['bitrate_open'].replace('DSQ_IDS;', c('r1', ids))
    for name, source in extension['codes'].items():
        plan.put(f[name], assemble(source, f[name], SIZES[name]))
    plan.features['capture_probe'] = dict(
        version=VERSION, page_index=PAGE_INDEX, magic=hex(MAGIC), file=LOG_NAME,
        page_offset=extension['page_offset'], state_offset=extension['state_offset'],
        allocation_size=extension['allocation_size'], state_bytes=STATE_BYTES,
        dump_offset=DUMP_OFFSET, dump_bytes=DUMP_BYTES, header_bytes=HEADER,
        capacity=CAPACITY, stride=STRIDE, key_offset=KEY_OFFSET, key_words=KEY_WORDS,
        max_phase=MAX_PHASE, labels=list(LABELS), statuses=list(STATUS),
        functions={k: hex(f[k] | 1) for k in SIZES}, controls=list(identifiers),
        hooks=[hex(b[0]) for b in BUILDERS], info_offsets=[hex(o) for o in INFO_OFFSETS], dump_bytes_per_record=DUMP, geometry_writes=False, capture_file_io=False,
        camera_verified=False,
        limitations=['Observation only; records start after the Extensions menu is first opened.',
                     f'At most {CAPACITY} distinct records; further distinct calls are counted as dropped.',
                     'Only the six capture-stage builders are observed; descriptor fields are recorded raw.'])
