import unittest
from types import SimpleNamespace

from jellyfin_subtitle_queue import item_matches_filters


def args(**overrides):
    values = {
        "title": None,
        "path": None,
        "season": None,
        "episode": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class QueueFilterTests(unittest.TestCase):
    def setUp(self):
        self.episode = {
            "Type": "Episode",
            "Name": "The Dollhouse",
            "SeriesName": "Yellowjackets",
            "Path": r"X:\\TV\\Yellowjackets\\Season 02\\Yellowjackets S02E04.mkv",
            "ParentIndexNumber": 2,
            "IndexNumber": 4,
        }

    def test_title_matches_series_name_case_insensitively(self):
        self.assertTrue(item_matches_filters(self.episode, args(title="yellowJACKETS")))

    def test_title_matches_episode_name(self):
        self.assertTrue(item_matches_filters(self.episode, args(title="dollhouse")))

    def test_title_rejects_unrelated_item(self):
        self.assertFalse(item_matches_filters(self.episode, args(title="Breaking Bad")))

    def test_path_matches_case_insensitively_and_normalizes_slashes(self):
        self.assertTrue(item_matches_filters(
            self.episode, args(path="/tv/yellowjackets/season 02/")
        ))

    def test_season_and_episode_use_jellyfin_metadata(self):
        self.assertTrue(item_matches_filters(
            self.episode, args(title="Yellowjackets", season=2, episode=4)
        ))
        self.assertFalse(item_matches_filters(self.episode, args(season=1)))
        self.assertFalse(item_matches_filters(self.episode, args(episode=5, season=2)))

    def test_season_filter_excludes_movies(self):
        movie = {"Type": "Movie", "Name": "Yellowjackets"}
        self.assertFalse(item_matches_filters(movie, args(title="Yellowjackets", season=2)))


if __name__ == "__main__":
    unittest.main()
