"""Ordering tests do not require Jellyfin, CUDA, or transcription packages."""
import unittest

from queue_order import item_sort_key


class QueueOrderTests(unittest.TestCase):
    def test_movies_are_alphabetical_case_insensitive(self):
        items = [
            {"Type": "Movie", "Name": "Zodiac", "Id": "z"},
            {"Type": "Movie", "Name": "alien", "Id": "a"},
            {"Type": "Movie", "Name": "Beetlejuice", "Id": "b"},
        ]
        self.assertEqual(
            [i["Id"] for i in sorted(items, key=item_sort_key)], ["a", "b", "z"]
        )

    def test_series_episodes_sorted_by_season_and_episode(self):
        items = [
            {"Type": "Episode", "SeriesName": "Dexter", "Name": "Finale",
             "ParentIndexNumber": 2, "IndexNumber": 2, "Id": "d22"},
            {"Type": "Episode", "SeriesName": "Dexter", "Name": "Pilot",
             "ParentIndexNumber": 1, "IndexNumber": 1, "Id": "d11"},
            {"Type": "Episode", "SeriesName": "Dexter", "Name": "Other",
             "ParentIndexNumber": 2, "IndexNumber": 1, "Id": "d21"},
            {"Type": "Episode", "SeriesName": "Dexter", "Name": "Second",
             "ParentIndexNumber": 1, "IndexNumber": 2, "Id": "d12"},
        ]
        self.assertEqual(
            [i["Id"] for i in sorted(items, key=item_sort_key)],
            ["d11", "d12", "d21", "d22"],
        )

    def test_mixed_movies_and_series_sorted_by_title(self):
        items = [
            {"Type": "Episode", "SeriesName": "Yellowjackets", "Name": "Pilot",
             "ParentIndexNumber": 1, "IndexNumber": 1, "Id": "y"},
            {"Type": "Movie", "Name": "Arrival", "Id": "a"},
            {"Type": "Episode", "SeriesName": "Better Call Saul", "Name": "Pilot",
             "ParentIndexNumber": 1, "IndexNumber": 1, "Id": "b"},
            {"Type": "Movie", "Name": "Moon", "Id": "m"},
        ]
        self.assertEqual(
            [i["Id"] for i in sorted(items, key=item_sort_key)],
            ["a", "b", "m", "y"],
        )

    def test_missing_episode_numbers_follow_known_numbers(self):
        items = [
            {"Type": "Episode", "SeriesName": "Show", "Name": "Unknown", "Id": "u"},
            {"Type": "Episode", "SeriesName": "Show", "Name": "Known",
             "ParentIndexNumber": 1, "IndexNumber": 1, "Id": "k"},
        ]
        self.assertEqual(
            [i["Id"] for i in sorted(items, key=item_sort_key)], ["k", "u"]
        )

    def test_order_is_deterministic_with_missing_metadata(self):
        items = [
            {"Type": "Episode", "Name": "Pilot", "Id": "z"},
            {"Type": "Episode", "Name": "Pilot", "Id": "a"},
        ]
        self.assertEqual(
            [i["Id"] for i in sorted(items, key=item_sort_key)], ["a", "z"]
        )


if __name__ == "__main__":
    unittest.main()
