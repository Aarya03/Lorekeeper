import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lorekeeper import narrator, runtime
from lorekeeper.projection import learn_lore


class LoreLinkedPuzzleTests(unittest.TestCase):
    def test_offline_fallback_is_related_but_not_copied_from_chat(self):
        source = "Remember when a glowing streak with a tail crossed the sky over the observatory? Best science night ever"
        with patch.dict("os.environ", {"EMBEDDING_PROVIDER": "demo_lexicon"}):
            word = narrator._fallback_signal_word(
                "Chat keeps returning to glowing streak tail crossed sky over observatory.", [source])
        self.assertNotIn(word, source.casefold().split())
        self.assertIn(word, {"comet", "meteor", "orbit", "star", "planet", "moon", "galaxy", "telescope"})

    def test_model_cannot_choose_word_only_present_in_charm_name(self):
        model_result = {
            "target_word": "charm",
            "clue": "A familiar keepsake is part of the story.",
            "hints": ["Think of an object.", "It is something a viewer might keep.", "It is a small token."],
            "reveal": "The charm carries the memory forward.",
        }
        source = "Remember when a comet crossed the observatory sky?"
        with patch.dict("os.environ", {"EMBEDDING_PROVIDER": "demo_lexicon"}), \
                patch.object(narrator, "_provider", return_value="lm_studio"), \
                patch.object(narrator, "_completion", return_value=model_result):
            puzzle = narrator.signal_puzzle(
                "Chat keeps returning to comet crossed the observatory sky", "Remember Comet Signal Charm", "The Sky Echo",
                source_texts=[source],
            )
        self.assertNotIn(puzzle["target_word"], source.casefold().split())
        self.assertIn(puzzle["target_word"], {"meteor", "orbit", "star", "planet", "moon", "galaxy", "telescope"})
        self.assertEqual(puzzle["provider"], "templates")

    def test_mistyped_remember_cue_creates_pending_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "lore-test.db"
            with patch.object(runtime, "DB_PATH", database), patch.object(runtime, "DATABASE_URL", ""):
                runtime.init_db()
                with runtime.db() as connection:
                    learn_lore(connection, "viewer-1", "Viewer", "Remeber when I had a dram about Mars", "event-1")
                with runtime.db() as connection:
                    row = connection.execute("SELECT summary,status FROM lore").fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
