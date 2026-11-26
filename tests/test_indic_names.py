import unittest

from lumen_ex.detection.indic_names import (
    contains_devanagari,
    names_match,
    normalize_name,
    transliterate_to_latin,
)


class IndicNameTests(unittest.TestCase):
    def test_detects_devanagari_without_misclassifying_latin(self) -> None:
        self.assertTrue(contains_devanagari("नाम: शाहिद"))
        self.assertFalse(contains_devanagari("Name: Shahid"))

    def test_transliterates_devanagari_runs_and_preserves_surrounding_text(self) -> None:
        result = transliterate_to_latin("Patient: शाहिद Khan")
        self.assertTrue(result.startswith("Patient: "))
        self.assertNotIn("शाहिद", result)
        self.assertIn("Khan", result)

    def test_matches_devanagari_with_its_direct_latin_transliteration(self) -> None:
        self.assertTrue(names_match("मोहम्मद शाहिद", "Mohammad Shahid"))
        self.assertTrue(names_match("मुहम्मद शाहिद", "Muhammad Shahid"))

    def test_normalizes_common_mohammad_spelling_variants(self) -> None:
        variants = (
            "मोहम्मद शाहिद",
            "Mohammad Shahid",
            "Mohammed Shahid",
            "Muhammad Shahid",
            "Mohd. Shahid",
            "Md. Shahid",
        )
        for variant in variants[1:]:
            with self.subTest(variant=variant):
                self.assertTrue(names_match(variants[0], variant))

    def test_preserves_long_final_vowels_in_devanagari_names(self) -> None:
        self.assertTrue(names_match("अमीना", "Amina"))

    def test_matches_complete_names_not_shared_substrings(self) -> None:
        self.assertFalse(names_match("Shahid Khan", "Shahid"))
        self.assertFalse(names_match("", "Shahid"))

    def test_normalizes_case_diacritics_and_punctuation(self) -> None:
        self.assertEqual(normalize_name("Śāhīd-Khān"), "shahid khan")


if __name__ == "__main__":
    unittest.main()