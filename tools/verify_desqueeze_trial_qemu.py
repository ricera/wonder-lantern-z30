"""Render the desqueeze trial menu in assisted QEMU and exercise its native rows.

Applies the candidate edits/tail to the running stock module (as the other
*_qemu verifiers do), opens Extensions -> Desqueeze, selects 1.33x and then
OFF through the native rows (each must save and return to Extensions), and
checks the patched display-fit window is in place. Not a camera test: no
sensor, ISP, live LCD scaling or SD card.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('qemu-root', 'image', 'report', 'fixture', 'output-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.qemu_root.resolve() / 'tools'))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from display_session import Session, png_from_ppm
    image = args.image.read_bytes()
    report = json.loads(args.report.read_text())
    fixture = json.loads(args.fixture.read_text())
    feature = report['features']['desqueeze']
    sound = report['features']['shutter_sound']
    try:
        session = Session(str(args.output_dir.resolve()), fresh=True)
    except TypeError:  # Older harness: prefix relative to <qemu-root>/.local, no fresh flag.
        session = Session(args.output_dir.name)
    result = dict(scope=__doc__, candidate_sha256=hashlib.sha256(image).hexdigest(), camera_verified=False)

    def write(address, data):
        for offset in range(0, len(data), 1024):
            part = data[offset:offset + 1024]
            assert session.remote.command(f'M{address + offset:x},{len(part):x}:' + part.hex()) == 'OK'
        assert session.remote.memory(address, len(data)) == data

    def word(address):
        return int.from_bytes(session.remote.memory(address, 4), 'little')

    def current():
        return int(session.input(0x1046109A, [])['registers']['r0'], 16)

    def frame(name):
        samples = []
        for index in range(6):
            session.run_for(.25)
            raw = (session.graphics / 'gui-latest.ppm').read_bytes()
            pixels = raw.split(b'\n', 3)[3]
            white = sum(all(v >= 190 for v in pixels[j:j + 3]) for j in range(0, len(pixels), 3))
            samples.append((white, index, raw))
        best = max(samples, key=lambda item: item[:2])
        (session.out / (name + '.png')).write_bytes(png_from_ppm(best[2]))
        return best[0]

    def row(index, name):
        page = current()
        session.input(0x1045D54C, [page, feature['controls'][index]])
        session.input(0x103CE96E, [0x27E])
        session.run_for(1)
        returned = int.from_bytes(session.remote.memory(current() + 2, 2), 'little')
        assert returned == 955, f'{name} did not return to Extensions ({returned})'
        return frame(name)

    try:
        scatter = []
        for edit in report['edits']:
            address = 0x10020000 + int(edit['offset'], 16)
            old, new = bytes.fromhex(edit['expected_hex']), bytes.fromhex(edit['replacement_hex'])
            assert session.remote.memory(address, len(old)) == old
            write(address, new)
            if 0x11F81C94 <= address and address + len(old) <= 0x12079EEC:
                scatter.append((0xC1800000 + address - 0x11F81C94, new))
        size = (args.qemu_root / '.local/firmware/eg2090_0120a0.bi').stat().st_size
        write(0x10020000 + size - 2, image[size - 2:])
        session.input(0x102355EE, [0xC182E688, 1])
        session.run_for(2)
        for address, data in scatter:
            write(address, data)
        for service in fixture['services']:
            write(service['address'], bytes.fromhex(service['code']))
            write(service['site'], bytes.fromhex(service['jump']))
        write(fixture['config'], bytes(16))
        write(fixture['wav_address'], bytes.fromhex(fixture['wav']))
        session.input(0x10465990, [0x1400, 3])
        session.input(0x10452CC0, [1])
        session.input(0x1045F732, [2, 397])
        session.input(int(sound['functions']['open'], 16) & ~1, [])
        session.run_for(1)
        frame('01-extensions')
        functions = {k: int(v, 16) & ~1 for k, v in feature['functions'].items()}
        site = int(feature['site'], 16)
        jump_ok = session.remote.memory(site, 10) != bytes.fromhex(feature['site_displaced'])
        session.input(functions['desqueeze_open'], [])
        session.run_for(1)
        page = current()
        assert int.from_bytes(session.remote.memory(page + 2, 2), 'little') == feature['page_index']
        frame('02-desqueeze')
        nv_address = int(feature['nv_address'], 16)
        nv = lambda: session.remote.memory(nv_address, 2)
        group_before = session.remote.memory(nv_address - 0x1E, 0x80)
        nv_boot = nv()
        state = int(session.input(functions['desqueeze_get'], [])['registers']['r0'], 16)
        words = feature['state_words']
        row(1, '03-select-1.33x')
        assert nv() == b'\x01\xfe', f'1.33x not stored ({nv().hex()})'
        assert word(state + words['magic']) == int(feature['state_magic'], 16), 'relayout state not initialized'
        pending_after_select = word(state + words['pending'])
        forced_after_select = word(state + words['forced'])
        session.run_for(3)
        pending_after_wait = word(state + words['pending'])
        forced_after_wait = word(state + words['forced'])
        assert pending_after_select == 1 or forced_after_select >= 1, 'factor change did not request a relayout'
        group_after = session.remote.memory(nv_address - 0x1E, 0x80)
        assert group_after[:0x1E] + group_after[0x20:] == group_before[:0x1E] + group_before[0x20:], \
            'group 0x01 neighbours changed'
        session.input(functions['desqueeze_open'], [])
        session.run_for(1)
        frame('04-desqueeze-1.33x')
        jpeg_row = len(feature['names'])
        row(jpeg_row, '05-jpeg-on')
        assert nv() == b'\x11\xee', f'JPEG flag not set ({nv().hex()})'
        session.input(functions['desqueeze_open'], [])
        session.run_for(1)
        frame('06-desqueeze-jpeg-on')
        row(jpeg_row, '07-jpeg-off')
        assert nv() == b'\x01\xfe', f'JPEG flag not cleared ({nv().hex()})'
        session.input(functions['desqueeze_open'], [])
        session.run_for(1)
        row(0, '05-select-off')
        assert nv() == bytes(2), 'OFF not stored'
        result.update(menu_page=feature['page_index'], site_patched=jump_ok,
                      nv_at_boot=nv_boot.hex(), group01_at_boot=group_before.hex(),
                      relayout=dict(pending_after_select=pending_after_select, forced_after_select=forced_after_select,
                                    pending_after_3s=pending_after_wait, forced_after_3s=forced_after_wait),
                      native_rows=dict(select_133=True, jpeg_on=True, jpeg_off=True, select_off=True),
                      images=['01-extensions.png', '02-desqueeze.png', '03-select-1.33x.png',
                              '04-desqueeze-1.33x.png', '05-jpeg-on.png', '06-desqueeze-jpeg-on.png', '07-jpeg-off.png', '05-select-off.png'])
    finally:
        (session.out / 'checks.json').write_text(json.dumps(result, indent=2, default=str) + '\n')
        session.close()
    print(json.dumps({k: v for k, v in result.items() if k != 'scope'}, indent=2, default=str))


if __name__ == '__main__':
    main()
