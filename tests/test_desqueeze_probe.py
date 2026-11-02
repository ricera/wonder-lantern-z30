import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from z30_extensions.features import desqueeze_probe as dp
from read_desqueeze_probe import CALLERS, decode, table


def synthetic_log(records):
    state = bytearray(dp.STATE_BYTES)
    struct.pack_into('<11I', state, dp.H_MAGIC, dp.MAGIC, dp.VERSION, 2, 2, 99, len(records),
                     0, 0, 3, 5, 0x1018AF73)
    struct.pack_into('<3I', state, dp.H_CAPACITY, dp.CAPACITY, dp.STRIDE, dp.HEADER)
    for i, (phase, caller, rect, mask) in enumerate(records):
        r = dp.HEADER + i * dp.STRIDE
        struct.pack_into('<9I', state, r, 10 + i, 2, 20 + i, phase, caller,
                         0 | 0 << 8 | 2 << 16 | 1 << 24, 0x8C3, 5568 | 3128 << 16, 720 | 480 << 16)
        struct.pack_into('<4h', state, r + 36, 0, 0, 720, 480)
        struct.pack_into('<2I', state, r + 44, 1, 1)
        struct.pack_into('<4h', state, r + 52, *rect)
        struct.pack_into('<6H', state, r + 60, 720, 480, 0, 0, 720, 480)
        struct.pack_into('<2I', state, r + 72, mask, 3 | 5 << 8)
    return bytes(state[dp.DUMP_OFFSET:])


class DesqueezeProbeLogTests(unittest.TestCase):
    def test_layout_constants_fit_the_record(self):
        self.assertEqual(dp.KEY_OFFSET + dp.KEY_WORDS * 4, dp.STRIDE)
        self.assertEqual(dp.DUMP_BYTES, dp.STATE_BYTES - dp.DUMP_OFFSET)
        self.assertLessEqual(dp.H_HEADER + 4, dp.HEADER)
        self.assertEqual(len(dp.STATUS), dp.S_CLEARED + 1)

    def test_decode_round_trip_and_ordering(self):
        data = synthetic_log([(1, 0x1018AFDB, (0, 36, 720, 442), 1),
                              (2, 0x10196DA5, (40, 0, 680, 480), 0xFFFFFFFF)])
        self.assertEqual(len(data), dp.DUMP_BYTES)
        log = decode(data)
        self.assertEqual(log['records_used'], 2)
        first, second = log['records']
        self.assertEqual(first['caller_name'], CALLERS[0x1018AFDB])
        self.assertEqual(first['cluster_mask'], 1)
        self.assertEqual(first['ratio_class_name'], '16:9')
        self.assertEqual(first['image_wh'], [5568, 3128])
        self.assertEqual(first['display_wh'], [720, 406])
        self.assertIsNone(second['cluster_mask'])
        self.assertEqual(second['display_aspect'], round(640 / 480, 4))
        self.assertIn('cluster B #1', table(log))

    def test_decode_rejects_wrong_magic_or_size(self):
        data = bytearray(synthetic_log([]))
        with self.assertRaises(ValueError):
            decode(bytes(data[:-4]))
        data[0] ^= 1
        with self.assertRaises(ValueError):
            decode(bytes(data))


if __name__ == '__main__':
    unittest.main()
