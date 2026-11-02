import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from z30_extensions.features import capture_probe as cp
from read_capture_probe import decode, table


class CaptureProbeLogTests(unittest.TestCase):
    def test_layout(self):
        self.assertEqual(cp.STRIDE, cp.KEY_OFFSET + cp.KEY_WORDS * 4 + cp.DUMP)
        self.assertEqual(cp.STRIDE % 4, 0)
        self.assertLessEqual(len(cp.INFO_OFFSETS), 16)
        for address, length, expected, _ in cp.BUILDERS:
            self.assertEqual(len(bytes.fromhex(expected)), length)

    def test_decode_round_trip(self):
        state = bytearray(cp.STATE_BYTES)
        struct.pack_into('<9I', state, cp.H_MAGIC, cp.MAGIC, cp.VERSION, 2, 2, 7, 1, 0, 0, 0)
        struct.pack_into('<3I', state, cp.H_CAPACITY, cp.CAPACITY, cp.STRIDE, cp.HEADER)
        r = cp.HEADER
        struct.pack_into('<6I', state, r, 5, 1, 5, 2, 4, 0x10143709)
        state[r + 24 + 6] = 1  # size index M
        struct.pack_into('<4H', state, r + 40 + 0x42, 4176, 2784, 0, 0)
        log = decode(bytes(state[cp.DUMP_OFFSET:]))
        rec = log['records'][0]
        self.assertEqual(rec['builder'], '0x1014319e')
        self.assertEqual(rec['info']['size229'], 1)
        self.assertIn(('0x42', 4176, 2784), rec['size_pairs'])
        self.assertIn('0x1014319e', table(log))


if __name__ == '__main__':
    unittest.main()
