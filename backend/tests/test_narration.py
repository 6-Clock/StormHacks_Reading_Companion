import unittest

from app.services.narration import (
    fallback_cues,
    fallback_moods,
    parse_cues,
    parse_moods,
    split_sentences,
)


class NarrationMoodTests(unittest.TestCase):
    def test_fallback_classifies_scary_and_warm_paragraphs(self) -> None:
        self.assertEqual(
            fallback_moods([
                "A shadow moved in the dark hall.",
                "Her friend gave her a warm smile.",
                "The book lay on the table.",
            ]),
            ["suspense", "warm", "neutral"],
        )

    def test_model_output_must_match_paragraph_count_and_vocabulary(self) -> None:
        self.assertEqual(parse_moods('{"moods":["warm","suspense"]}', 2), ["warm", "suspense"])
        self.assertIsNone(parse_moods('{"moods":["warm"]}', 2))
        self.assertIsNone(parse_moods('{"moods":[{"mood":"warm"}]}', 1))
        self.assertIsNone(parse_moods("not json", 1))

    def test_literal_event_cues_use_sentence_indexes(self) -> None:
        sentences = [
            split_sentences("The hall was silent. The door creaked open. Mara froze."),
            split_sentences("A door to new opportunities opened."),
        ]
        self.assertEqual(len(sentences[0]), 3)
        self.assertEqual(
            fallback_cues(sentences),
            [{"paragraph_index": 0, "sentence_index": 1, "effect": "door_creak"}],
        )
        self.assertEqual(
            parse_cues(
                '{"cues":[{"paragraph_index":0,"sentence_index":1,"effect":"door_creak"}]}',
                sentences,
            ),
            fallback_cues(sentences),
        )
        self.assertIsNone(
            parse_cues(
                '{"cues":[{"paragraph_index":0,"sentence_index":1,"effect":"explosion"}]}',
                sentences,
            )
        )


if __name__ == "__main__":
    unittest.main()
