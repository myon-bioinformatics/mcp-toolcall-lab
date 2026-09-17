import unittest

from mcp_toolcall_lab.catalog import (
    AVAILABLE_TOOLS,
    search_municipalities,
    search_stations,
    search_transaction_prices,
)


class MockCatalogTest(unittest.TestCase):
    def test_mock_catalog_has_multiple_tools(self):
        self.assertEqual(
            AVAILABLE_TOOLS,
            ("find_municipalities", "find_transaction_prices", "find_stations"),
        )

    def test_municipality_search_is_deterministic(self):
        self.assertEqual(
            search_municipalities("yokohama"),
            [{"code": "14109", "name": "Yokohama", "prefecture": "Kanagawa"}],
        )

    def test_blank_municipality_query_is_empty_not_error(self):
        self.assertEqual(search_municipalities("  "), [])

    def test_japanese_municipality_query_is_empty_in_this_mock(self):
        self.assertEqual(search_municipalities("横浜"), [])

    def test_transaction_price_is_mock_data(self):
        result = search_transaction_prices("14109", 2025)
        self.assertEqual(result[0]["source"], "mock")
        self.assertEqual(result[0]["year"], 2025)

    def test_unknown_municipality_transaction_prices_are_empty(self):
        self.assertEqual(search_transaction_prices("00000", 2025), [])

    def test_station_search_for_unknown_municipality_is_empty(self):
        self.assertEqual(search_stations("00000"), [])
