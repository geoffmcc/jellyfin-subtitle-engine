import unittest
from types import SimpleNamespace

import speaker_finalize
import speaker_sentence


class ConversationalBoundaryTests(unittest.TestCase):
    def test_turn_transition_allows_unpunctuated_exchange(self):
        self.assertEqual(
            speaker_sentence.classify_boundary("leaving", "now", 5, 4, 0.04, True),
            5,
        )

    def test_unpunctuated_text_without_turn_transition_is_not_split(self):
        self.assertIsNone(
            speaker_sentence.classify_boundary("leaving", "now", 5, 4, 0.04, False)
        )

    def test_short_reply_can_split_after_pause(self):
        self.assertEqual(
            speaker_sentence.classify_boundary("yeah", "because", 1, 5, 0.16, False),
            4,
        )

    def test_turn_transition_rejects_long_gap(self):
        self.assertIsNone(
            speaker_sentence.classify_boundary("leaving", "now", 5, 4, 1.5, True)
        )

    def test_short_but_real_turns_are_preserved(self):
        class Annotation:
            def itertracks(self, yield_label=False):
                return iter([
                    (SimpleNamespace(start=1.0, end=1.18), None, "SPEAKER_00"),
                    (SimpleNamespace(start=2.0, end=2.25), None, "SPEAKER_01"),
                    (SimpleNamespace(start=3.0, end=3.08), None, "SPEAKER_02"),
                ])

        self.assertEqual(
            speaker_finalize.serialize_speaker_turns(Annotation()),
            [
                {"start": 1.0, "end": 1.18, "speaker": "SPEAKER_00"},
                {"start": 2.0, "end": 2.25, "speaker": "SPEAKER_01"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
