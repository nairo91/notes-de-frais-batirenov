import os
import unittest

# On désactive l'initialisation DB pour tester la logique pure
os.environ["SKIP_INIT_DB"] = "1"

from app import generate_chambres


class GenerateChambresTests(unittest.TestCase):
    def test_default_generation_stops_at_526(self):
        chambres = generate_chambres()
        self.assertEqual("026", chambres[25])
        self.assertEqual("526", chambres[-1])
        self.assertEqual(6 * 26, len(chambres))

    def test_clamps_room_count_to_26(self):
        chambres = generate_chambres(max_floor=1, max_room_number=40)
        self.assertEqual("026", chambres[25])
        self.assertEqual("126", chambres[-1])
        self.assertNotIn("127", chambres)

    def test_negative_values_return_empty_list(self):
        self.assertEqual([], generate_chambres(max_floor=-1, max_room_number=-5))


if __name__ == "__main__":
    unittest.main()
