import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from maine_agent import towns
from maine_agent.geomath import bearing_deg, destination_point, haversine_m


class TestTowns(unittest.TestCase):
    def test_known_town(self):
        self.assertEqual(towns.county_for_city("Camden"), "Knox")
        self.assertEqual(towns.county_for_city("bar harbor"), "Hancock")

    def test_village_alias(self):
        self.assertEqual(towns.county_for_city("East Boothbay"), "Lincoln")

    def test_unknown_town(self):
        self.assertIsNone(towns.county_for_city("Portland"))  # Cumberland County, not tracked
        self.assertIsNone(towns.county_for_city(""))
        self.assertIsNone(towns.county_for_city(None))

    def test_no_duplicate_towns_across_counties(self):
        seen = {}
        for county, names in towns._TOWNS.items():
            for n in names:
                key = n.strip().lower()
                self.assertNotIn(key, seen, f"{n} listed in both {seen.get(key)} and {county}")
                seen[key] = county


class TestGeomath(unittest.TestCase):
    def test_haversine_zero(self):
        self.assertAlmostEqual(haversine_m(44.0, -69.0, 44.0, -69.0), 0.0, places=3)

    def test_destination_round_trip(self):
        lat, lon = 44.21, -69.06
        for bearing in [0, 45, 90, 180, 270]:
            dlat, dlon = destination_point(lat, lon, bearing, 1000)
            d = haversine_m(lat, lon, dlat, dlon)
            self.assertAlmostEqual(d, 1000, delta=1.0)

    def test_bearing_north(self):
        b = bearing_deg(44.0, -69.0, 44.1, -69.0)
        self.assertAlmostEqual(b, 0.0, delta=0.5)


if __name__ == "__main__":
    unittest.main()
