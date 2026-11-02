import unittest

from z30_extensions.features import desqueeze_ui as du
from z30_extensions.features import aspect_ui


class DesqueezeUiLayoutTests(unittest.TestCase):
    def test_text_slots_do_not_overlap(self):
        n = len(du.ROW_LABELS)
        labels = set(range(du.LABEL_SLOT, du.LABEL_SLOT + n))
        marks = set(range(du.MARK_SLOT, du.MARK_SLOT + n))
        fixed = {du.TITLE_SLOT, du.ROOT_SLOT, du.CURRENT_SLOT}
        self.assertFalse(labels & marks or labels & fixed or marks & fixed)
        self.assertLess(max(labels | marks | fixed), aspect_ui.TITLE_SLOT)

    def test_factors_match_names_and_widen(self):
        self.assertEqual(len(du.FACTORS) + 1, len(du.NAMES))
        for num, den in du.FACTORS:
            self.assertGreater(num, den)
            self.assertLessEqual(1920 * num // den, 0x7FFF)

    def test_site_and_nv_layout(self):
        self.assertEqual(du.SITE_CONTINUE - du.SITE, du.SITE_LENGTH)
        self.assertEqual(len(du.SITE_EXPECTED), du.SITE_LENGTH)
        self.assertEqual(du.NV_ADDRESS % 2, 0)
        self.assertTrue(du.NV_GROUP_RAM <= du.NV_ADDRESS and
                        du.NV_ADDRESS + 2 <= du.NV_GROUP_RAM + du.NV_GROUP_SIZE)

    def test_nv_encoding_rejects_zero_default_as_factor(self):
        # Factory default bytes 00 00 must decode as OFF, never as a factor.
        index, check = 0, 0
        self.assertNotEqual(check, index ^ 0xFF)

    def test_desqueezed_previews_keep_width_and_shrink_height(self):
        for factor in du.FACTORS:
            for stock in (du.STOCK_SMALL, du.STOCK_LARGE):
                for (w, h), (nw, nh) in zip(stock, du.desqueezed(stock, factor)):
                    self.assertEqual(w, nw)
                    self.assertLess(nh, h)
                    self.assertEqual(nh % 4, 0)
                    self.assertLessEqual(abs(nh - h * factor[1] / factor[0]), 2)
        self.assertEqual(du.desqueezed(du.STOCK_LARGE, (4, 3))[0], (1620, 808))


    def test_jpeg_sizes_keep_width_and_align(self):
        for factor in du.FACTORS:
            for area in du.STOCK_JPEG:
                for (w, h), (nw, nh) in zip(area, du.desqueezed16(area, factor)):
                    self.assertEqual(w, nw)
                    self.assertLess(nh, h)
                    self.assertEqual(nh % 16, 0)
                    self.assertLessEqual(abs(nh - h * factor[1] / factor[0]), 8)
        self.assertEqual(du.desqueezed16(du.STOCK_JPEG[0], (4, 3))[0], (5568, 2784))

    def test_nv_flag_does_not_collide_with_index(self):
        self.assertGreater(du.NV_JPEG, len(du.NAMES) - 1)
        self.assertEqual(du.NV_JPEG & 7, 0)
        self.assertEqual(du.ROW_LABELS[du.JPEG_ROW], 'Desqueeze JPEG')


if __name__ == '__main__':
    unittest.main()
