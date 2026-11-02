import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from z30_extensions.features import playback_probe as pp
from read_playback_probe import decode, table


def synthetic_log(records):
    state = bytearray(pp.STATE_BYTES)
    struct.pack_into('<9I', state, pp.H_MAGIC, pp.MAGIC, pp.VERSION, 3, 3, 50, len(records), 0, 0, 0)
    struct.pack_into('<3I', state, pp.H_CAPACITY, pp.CAPACITY, pp.STRIDE, pp.HEADER)
    for i, (phase, caller, src, result) in enumerate(records):
        r = pp.HEADER + i * pp.STRIDE
        struct.pack_into('<8I', state, r, 10 + i, 4, 20 + i, phase, caller, 0x20,
                         src[0] | src[1] << 16, 720 | 480 << 16)
        struct.pack_into('<4h', state, r + 32, 0, 0, 720, 480)
        struct.pack_into('<4h', state, r + 40, *result)
    return bytes(state[pp.DUMP_OFFSET:])


class PlaybackProbeLogTests(unittest.TestCase):
    def test_layout_constants(self):
        self.assertEqual(pp.KEY_OFFSET + pp.KEY_WORDS * 4, pp.STRIDE)
        self.assertEqual(pp.DUMP_BYTES, pp.STATE_BYTES - pp.DUMP_OFFSET)
        self.assertEqual(len(bytes.fromhex(pp.FIT_EXPECTED)), pp.FIT_LENGTH)
        self.assertEqual(pp.FIT_CONTINUE - pp.FIT, pp.FIT_LENGTH)

    def test_decode_round_trip(self):
        data = synthetic_log([(3, 0x1039C73F, (6000, 4000), (0, 0, 720, 480)),
                              (1, 0x1018AF11, (5568, 3712), (0, 36, 720, 442))])
        log = decode(data)
        self.assertEqual(log['records_used'], 2)
        first, second = log['records']
        self.assertEqual(first['source_wh'], [6000, 4000])
        self.assertEqual(first['result_aspect'], 1.5)
        self.assertTrue(second['caller_name'])
        self.assertIn('0x1039c73f', table(log))

    def test_rejects_other_logs(self):
        data = bytearray(synthetic_log([]))
        data[0] ^= 1
        with self.assertRaises(ValueError):
            decode(bytes(data))


if __name__ == '__main__':
    unittest.main()
