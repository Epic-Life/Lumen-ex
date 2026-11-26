import json
from pathlib import Path
import unicodedata
import unittest

from lumen_ex.contracts.identity import PatientIdentity
from lumen_ex.identity import (
    extract_fhir_identities,
    extract_identity,
    normalize_age,
    normalize_date,
    normalize_gender,
    normalize_identifier,
    normalize_location,
    normalize_phone,
)
from lumen_ex.detection.indic_names import names_match, normalize_name


_FIXTURES = Path(__file__).parent / "fixtures" / "identity_examples.json"


class NameNormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.examples = json.loads(_FIXTURES.read_text(encoding="utf-8"))

    def test_cross_script_and_hinglish_honorific_forms(self) -> None:
        for first, second in self.examples["cross_script_names"]:
            with self.subTest(first=first, second=second):
                self.assertTrue(names_match(first, second))

    def test_common_unicode_and_spelling_variants_normalize_without_name_aliases(self) -> None:
        for first, second in self.examples["spelling_and_unicode_variants"]:
            with self.subTest(first=first, second=second):
                self.assertTrue(names_match(first, second))

    def test_similar_or_partial_names_are_not_equal(self) -> None:
        for first, second in self.examples["distinct_names"]:
            with self.subTest(first=first, second=second):
                self.assertFalse(names_match(first, second))

    def test_normalization_keeps_initials_and_name_component_order(self) -> None:
        self.assertEqual(normalize_name("M. Shahid Khan"), "m shahid khan")
        self.assertNotEqual(normalize_name("Shahid Khan"), normalize_name("Khan Shahid"))


class ScalarNormalizationTests(unittest.TestCase):
    def test_normalizes_indian_phone_forms_and_rejects_malformed_values(self) -> None:
        self.assertEqual(normalize_phone("+91 98765-43210"), "+919876543210")
        self.assertEqual(normalize_phone("09876543210"), "+919876543210")
        self.assertEqual(normalize_phone("९८७६५४३२१०"), "+919876543210")
        self.assertIsNone(normalize_phone("12345"))
        self.assertIsNone(normalize_phone("call 9876543210"))

    def test_normalizes_dates_only_when_the_numeric_order_is_unambiguous(self) -> None:
        self.assertEqual(normalize_date("1982-04-13"), "1982-04-13")
        self.assertEqual(normalize_date("13/04/1982"), "1982-04-13")
        self.assertEqual(normalize_date("04/13/1982"), "1982-04-13")
        self.assertIsNone(normalize_date("04/05/1982"))
        self.assertIsNone(normalize_date("31/02/1982"))

    def test_normalizes_age_gender_identifiers_and_locations(self) -> None:
        self.assertEqual(normalize_age("48 years"), "48")
        self.assertIsNone(normalize_age("148"))
        self.assertEqual(normalize_gender("F"), "female")
        self.assertIsNone(normalize_gender("not specified"))
        self.assertEqual(normalize_identifier("१२३४-AB"), "1234AB")
        self.assertEqual(normalize_location("  Rae-Bareli  "), "rae bareli")


class IdentityExtractionTests(unittest.TestCase):
    def test_projects_csv_or_tsv_style_row_and_keeps_source_values(self) -> None:
        identity = extract_identity(
            {
                "Patient Name": "शाहिद खान",
                "PatientID": "SNJ-TEST-04",
                "DOB": "1982-04-13",
                "Age": 44,
                "Sex": "M",
                "Mobile Number": "+91 98765 43210",
                "Address": "Ward 2, Rae Bareli",
                "Aadhaar No": "१२३४-५६७८-९०१२",
                "Diagnosis": "not identity data",
            },
            "registration/test.tsv",
            "row:0",
        )
        self.assertIsInstance(identity, PatientIdentity)
        self.assertEqual(identity.record_locator, "row:0")
        self.assertEqual(identity.name[0].original, "शाहिद खान")
        self.assertEqual(identity.name[0].normalized, "shahid khan")
        self.assertEqual(identity.date_of_birth[0].normalized, "1982-04-13")
        self.assertEqual(identity.phone[0].normalized, "+919876543210")
        self.assertEqual(identity.hospital_identifier[0].normalized, "SNJTEST04")
        self.assertEqual(identity.government_identifier[0].normalized, "123456789012")
        self.assertNotIn("Diagnosis", repr(identity))
        self.assertNotIn("not identity data", repr(identity))

    def test_extracts_nested_json_and_preserves_distinct_name_parts(self) -> None:
        identity = extract_identity(
            {
                "patient": {
                    "name": {"given": ["Geeta"], "family": "Chauhan"},
                    "contact": {"mobile": "9876543210"},
                    "address": {"line": ["House 2"], "district": "Rae Bareli"},
                    "ids": {"aadhar_no": 123456789012},
                },
                "doctor": {"name": "Dr. Ramesh"},
            },
            "registration/patients.json",
            "/patients/0",
        )
        originals = {value.original for value in identity.name}
        self.assertIn("Geeta", originals)
        self.assertIn("Chauhan", originals)
        self.assertTrue(any(value.original == "Geeta Chauhan" for value in identity.name))
        self.assertFalse(any(value.original == "Dr. Ramesh" for value in identity.name))
        self.assertTrue(identity.address)
        self.assertIn("Rae Bareli", {value.original for value in identity.location})
        self.assertEqual(identity.government_identifier[0].normalized, "123456789012")

    def test_extracts_fhir_patient_and_bundle_with_record_locators(self) -> None:
        bundle = {
            "resourceType": "Bundle",
            "entry": [
                {"resource": {"resourceType": "Observation", "id": "ignored"}},
                {
                    "resource": {
                        "resourceType": "Patient",
                        "name": [{"text": "Amina Khan", "given": ["Amina"], "family": "Khan"}],
                        "identifier": [
                            {"system": "urn:hospital:mrn", "value": "MRN-TEST-5"},
                            {"system": "urn:abha:number", "value": "ABHA-TEST-6"},
                        ],
                        "telecom": [{"system": "phone", "value": "9876543210"}],
                        "gender": "female",
                        "birthDate": "1982-04-13",
                        "address": [
                            {
                                "line": ["House 4"],
                                "city": "Lalganj",
                                "district": "Rae Bareli",
                                "postalCode": "229001",
                            }
                        ],
                    }
                },
            ],
        }
        identities = extract_fhir_identities(bundle, "fhir/bundle.json")
        self.assertEqual(len(identities), 1)
        patient = identities[0]
        self.assertEqual(patient.record_locator, "/entry/1/resource")
        self.assertEqual(patient.hospital_identifier[0].normalized, "MRNTEST5")
        self.assertEqual(patient.government_identifier[0].normalized, "ABHATEST6")
        self.assertEqual(patient.phone[0].normalized, "+919876543210")
        self.assertTrue(patient.address)
        self.assertTrue(patient.location)

    def test_extracts_only_explicitly_labeled_note_fields(self) -> None:
        identity = extract_identity(
            "नाम: मोहम्मद शाहिद\nउम्र: 48 वर्ष\nGender: M\n"
            "Phone: 98765 43210\nDOB: 1982-04-13\nUHID: JJH-TEST-7\n"
            "Asha ji ka BP 117/82",
            "notes/example.txt",
        )
        self.assertEqual(identity.name[0].normalized, "mohammad shahid")
        self.assertEqual(identity.age[0].normalized, "48")
        self.assertEqual(identity.gender[0].normalized, "male")
        self.assertEqual(identity.phone[0].normalized, "+919876543210")
        self.assertEqual(identity.hospital_identifier[0].normalized, "JJHTEST7")
        self.assertEqual(len(identity.name), 1)

    def test_reports_missing_and_malformed_values_without_dropping_originals(self) -> None:
        identity = extract_identity(
            {"Patient Name": "M. Shahid", "Phone": "123", "DOB": "31/02/1982"},
            "registration/partial.csv",
            "row:3",
        )
        self.assertIn("phone", identity.invalid_fields)
        self.assertIn("date_of_birth", identity.invalid_fields)
        self.assertIsNone(identity.phone[0].normalized)
        self.assertEqual(identity.phone[0].original, "123")
        self.assertIn("gender", identity.missing_fields)
        self.assertIn("hospital_identifier", identity.missing_fields)

    def test_unicode_composition_and_full_width_phone_are_normalized(self) -> None:
        composed = extract_identity(
            {"Name": "Śāhīd Khān", "Phone": "＋９１　９８７６５４３２１０"},
            "registration/unicode.json",
        )
        decomposed = extract_identity(
            {"Name": unicodedata.normalize("NFD", "Śāhīd Khān")},
            "registration/unicode.json",
        )
        self.assertEqual(composed.name[0].normalized, "shahid khan")
        self.assertEqual(decomposed.name[0].normalized, composed.name[0].normalized)
        self.assertEqual(composed.phone[0].normalized, "+919876543210")


if __name__ == "__main__":
    unittest.main()
