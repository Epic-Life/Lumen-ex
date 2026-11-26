import unittest

from lumen_ex.label_redaction import redact_labeled_names


class HinglishRedactionTests(unittest.TestCase):
    def test_removes_honorific_names_from_reports_and_messages(self):
        text = (
            "[18/04/26, 9:00 am] Dev Bhaiya: report attached\n"
            "Report of Tara Kapoor behenji 55 Y / F\n"
            "Tara ji ka BP 130/85 mmHg. Dolo 650 BD x 5 days.\n"
        )
        self.assertEqual(
            redact_labeled_names(text),
            (
                "[18/04/26, 9:00 am] [NAME]: report attached\n"
                "Report of [NAME] 55 Y / F\n"
                "[NAME] ka BP 130/85 mmHg. Dolo 650 BD x 5 days.\n",
                3,
            ),
        )

    def test_preserves_clinicians_and_complaints_while_removing_relatives(self):
        text = (
            "C/O fever; C/O Chest Pain; C/O Bell's palsy; "
            "K/C/O Dengue. Attendant (W/o Dev Kapoor).\n"
            "Address (guardian): C/O Dev Kapoor\n"
            "पिता का नाम: देव कपूर\n"
            "Examined by Dr. Tara Kapoor ji and Doctor Dev sahab."
        )
        self.assertEqual(
            redact_labeled_names(text),
            (
                "C/O fever; C/O Chest Pain; C/O Bell's palsy; "
                "K/C/O Dengue. Attendant (W/o [NAME]).\n"
                "Address (guardian): C/O [NAME]\n"
                "पिता का नाम: [NAME]\n"
                "Examined by Dr. Tara Kapoor ji and Doctor Dev sahab.",
                3,
            ),
        )
