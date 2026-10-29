"""Deterministic unit tests for rank-calibrated semantic heat."""
import unittest

from lorekeeper.heat import score_guess, score_similarity


class RankHeatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Fixed reference distribution keeps these tests independent of LM Studio.
        cls.distribution = [-1.0 + index / 5000 for index in range(10001)]
        cls.target = [1.0, 0.0]

    def test_unrelated_word_scores_cold(self):
        score = score_guess([0.0, 1.0], self.target, self.distribution)
        self.assertLess(score["heat"], 20)

    def test_synonym_like_similarity_scores_hot(self):
        # A near-paraphrase should sit in the reference distribution's top 5%.
        score = score_similarity(0.98, self.distribution)
        self.assertGreater(score["heat"], 70)

    def test_heat_bands_follow_reference_percentile(self):
        cases = ((99.0, 90), (95.0, 70), (85.0, 40), (60.0, 15), (0.0, 0))
        for percentile, minimum in cases:
            index = round(percentile * (len(self.distribution) - 1) / 100)
            result = score_similarity(self.distribution[index], self.distribution)
            self.assertGreaterEqual(result["heat"], minimum, f"percentile {percentile}")

    def test_exact_word_is_solved(self):
        score = score_similarity(1.0, self.distribution, exact=True)
        self.assertEqual(score, {"heat": 100, "percentile": 100.0, "rank": 1})

    def test_results_are_deterministic(self):
        first = score_guess([0.8, 0.6], self.target, self.distribution)
        second = score_guess([0.8, 0.6], self.target, self.distribution)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
