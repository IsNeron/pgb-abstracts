"""Public source excerpts from pgb2026, fetched on 2026-10-28.

These are fixtures, never runtime submission-code overrides. Speaker emails are
excluded. Abstracts contain one unchanged source paragraph per submission.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml
from test_pipeline import CONFIG, ROOT

from esoil_proceedings.affiliations import AffiliationRegistry
from esoil_proceedings.normalize import normalize_submission
from esoil_proceedings.render import latex_richtext, render_latex
from esoil_proceedings.richtext import richtext_plain
from esoil_proceedings.validate import validate_submissions

REGISTRY = AffiliationRegistry(
    yaml.safe_load(CONFIG.read_text(encoding="utf8"))["normalization"]["affiliation_aliases"]
)
FIXTURES = json.loads((Path(__file__).parent / "fixtures/biota.json").read_text(encoding="utf8"))


class BiotaRegressionTests(unittest.TestCase):
    def setUp(self):
        self.values = {
            x["code"]: normalize_submission(x, affiliation_registry=REGISTRY) for x in FIXTURES
        }

    def test_all_excerpts_preserve_scientific_content(self):
        for source in FIXTURES:
            value = self.values[source["code"]]
            with self.subTest(code=value.code):

                def letters(text):
                    return "".join(c for c in text if c.isalnum())

                self.assertEqual(
                    letters(source["abstract"]), letters(richtext_plain(value.abstract))
                )
                record = validate_submissions([value])[0]
                self.assertFalse(record.errors)
                self.assertFalse(
                    any(i.type == "suspicious_author_affiliation_leak" for i in record.issues)
                )

    def test_university_variants_merge_before_markers(self):
        value = self.values["L9MMAF"]
        self.assertEqual(len(value.affiliations), 1)
        self.assertTrue(all(a.affiliations == [1] for a in value.authors))
        tex, _ = render_latex([value], CONFIG, ROOT)
        self.assertNotIn(r"Филиппов Д.Д.\textsuperscript", tex)

    def test_two_institutions_for_one_speaker(self):
        value = self.values["8PW38U"]
        self.assertEqual(value.authors[0].affiliations, [1, 2])
        self.assertEqual([a.affiliations for a in value.authors[1:]], [[2], [2]])
        tex, _ = render_latex([value], CONFIG, ROOT)
        self.assertIn(r"Деревенец Е.Н.\textsuperscript{1,2}", tex)

    def test_markdown_italic_and_scientific_scripts(self):
        tex = latex_richtext(self.values["MKRG9F"].abstract)
        self.assertIn(r"\textit{in situ}", tex)
        self.assertNotIn("*in situ*", tex)
        tex = latex_richtext(self.values["VGWWV7"].abstract)
        self.assertIn(r"10\textsuperscript{2}", tex)
        self.assertIn(r"\textit{Bacillus}", tex)
        tex = latex_richtext(self.values["8PW38U"].abstract)
        self.assertIn(r"СО\textsubscript{2}", tex)

    def test_keyword_terminal_punctuation(self):
        self.assertEqual(self.values["ZVUCKM"].keywords[-1], "антибиотикорезистентность")

    def test_comma_separated_and_semicolon_separated_institutions(self):
        value = self.values["TWWSQ3"]
        self.assertEqual(value.authors[0].affiliations, [1, 2])
        self.assertTrue(any(len(a.affiliations) == 2 for a in value.authors[1:]))

    def test_commas_inside_institution_dont_create_people(self):
        value = self.values["UG9N8B"]
        self.assertEqual(
            [a.name for a in value.authors], ["Архипов А.Е.", "Могилевская И.В.", "Фильцов Р.Н."]
        )
        self.assertEqual(len(value.affiliations), 1)


if __name__ == "__main__":
    unittest.main()
