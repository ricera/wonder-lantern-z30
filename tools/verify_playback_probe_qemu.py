"""Render the playback probe menu in assisted QEMU and exercise its native rows.

Applies the candidate edits/tail to the running stock module (as the other
*_qemu verifiers do), opens Extensions -> Playback probe, runs the native
"Next phase" and "Clear log" rows and requires each to return to Extensions.
Also reports any generic-fit records the assisted boot produced. Not a camera
test: no sensor, ISP, live LCD scaling or SD card.
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
    from read_playback_probe import decode
    image = args.image.read_bytes()
    report = json.loads(args.report.read_text())
    fixture = json.loads(args.fixture.read_text())
    feature = report['features']['playback_probe']
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
        session.input(functions['bitrate_open'], [])
        session.run_for(1)
        page = current()
        assert int.from_bytes(session.remote.memory(page + 2, 2), 'little') == feature['page_index']
        frame('02-playback-probe')
        state = int(session.input(functions['bitrate_get'], [])['registers']['r0'], 16)
        assert state, 'probe state not allocated'
        assert word(state + 8) == int(feature['magic'], 16), 'probe header not initialized'
        before = decode(session.remote.memory(state + feature['dump_offset'], feature['dump_bytes']))
        row(0, '03-next-phase')
        assert word(state + 16) == 1 and word(state + 20) == 1, 'Next phase did not advance'
        functions_ok = dict(next_phase=True)
        session.input(functions['bitrate_open'], [])
        session.run_for(1)
        row(2, '04-clear-log')
        assert word(state + 16) == 0 and word(state + 28) == 0 and word(state + 20) == 13
        functions_ok['clear_log'] = True
        result.update(menu_page=feature['page_index'], state=hex(state), native_rows=functions_ok,
                      boot_records=before['records'], boot_sequence=before['sequence'],
                      images=['01-extensions.png', '02-playback-probe.png',
                              '03-next-phase.png', '04-clear-log.png'])
    finally:
        (session.out / 'checks.json').write_text(json.dumps(result, indent=2, default=str) + '\n')
        session.close()
    print(json.dumps({k: v for k, v in result.items() if k != 'scope'}, indent=2, default=str))


if __name__ == '__main__':
    main()
