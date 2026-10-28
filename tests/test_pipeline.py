from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from esoil_proceedings.affiliations import AffiliationRegistry
from esoil_proceedings.cli import _check_build_validation
from esoil_proceedings.config import load_config
from esoil_proceedings.models import QAIssue
from esoil_proceedings.normalize import (
    normalize_keywords,
    normalize_person_name,
    normalize_submission,
    normalize_submissions,
    parse_coauthors,
)
from esoil_proceedings.render import (
    BuildError,
    build_display_model,
    latex_richtext,
    load_render_config,
    render_latex,
)
from esoil_proceedings.richtext import normalize_richtext, richtext_plain
from esoil_proceedings.storage import read_proceedings, write_proceedings, write_qa_reports
from esoil_proceedings.validate import validate_submissions

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/conference.yaml"
REGISTRY = AffiliationRegistry(
    yaml.safe_load(CONFIG.read_text(encoding="utf8"))["normalization"]["affiliation_aliases"]
)
MSU = "МГУ им. М.В. Ломоносова"


def raw(**changes):
    value = {
        "code": "TEST",
        "state": "confirmed",
        "title": "Научный текст",
        "track": {"id": 33, "name": "Биота", "position": 3},
        "submission_type": {"id": 1, "name": "Гласный доклад"},
        "speakers": [{"name": "Иванов И.И.", "biography": "МГУ имени М.В. Ломоносова"}],
        "abstract": "Исходное научное утверждение.",
        "description": "почва",
    }
    value.update(changes)
    return value


def with_coauthors(answer, **changes):
    return raw(answers=[{"question": {"identifier": "CDHXWQFZ"}, "answer": answer}], **changes)


def normalize(value):
    return normalize_submission(value, affiliation_registry=REGISTRY)


def issues(value):
    return validate_submissions([value])[0].issues


class RichtextTests(unittest.TestCase):
    def test_supported_inline_formats(self):
        for source, expected in [
            ("*italic*", r"\textit{italic}"),
            ("_italic_", r"\textit{italic}"),
            ("**bold**", r"\textbf{bold}"),
            ("<em>x</em>", r"\textit{x}"),
            ("<i>x</i>", r"\textit{x}"),
            ("<strong>x</strong>", r"\textbf{x}"),
            ("<b>x</b>", r"\textbf{x}"),
            ("10<sup>7</sup>", r"10\textsuperscript{7}"),
            ("CO<sub>2</sub>", r"CO\textsubscript{2}"),
        ]:
            with self.subTest(source=source):
                self.assertEqual(latex_richtext(normalize_richtext(source)).strip(), expected)

    def test_inline_whitespace(self):
        for source, expected in [
            ("foo <em>bar</em> baz", "foo bar baz"),
            ("foo<strong>bar</strong>", "foobar"),
            ("foo <sup>2</sup>", "foo 2"),
            ("микробная <em>активность</em>", "микробная активность"),
            ("<em>foo</em><b>bar</b>", "foobar"),
        ]:
            with self.subTest(source=source):
                self.assertEqual(richtext_plain(normalize_richtext(source)), expected)

    def test_paragraphs_and_wrapping(self):
        value = normalize_richtext("<p>first\nwrapped</p><p></p><p>second<br>line</p>")
        self.assertEqual(len(value["blocks"]), 2)
        self.assertEqual(richtext_plain(value), "first wrapped\n\nsecond\nline")
        self.assertEqual(latex_richtext(value).count(r"\par"), 1)
        self.assertIn(r"\newline{}", latex_richtext(value))
        self.assertEqual(len(normalize_richtext("first\nwrapped\n\nsecond")["blocks"]), 2)

    def test_source_scientific_markup_only(self):
        value = normalize_richtext("107 CO2 CO_2 10^7 δ^13C 10^{−8}")
        tex = latex_richtext(value)
        self.assertIn("107 CO2", tex)
        self.assertIn(r"CO\textsubscript{2}", tex)
        self.assertIn(r"10\textsuperscript{7}", tex)
        self.assertIn(r"δ\textsuperscript{13}C", tex)
        self.assertIn(r"10\textsuperscript{−8}", tex)

    def test_nested_markup_and_latex_escaping(self):
        tex = latex_richtext(normalize_richtext("**bold *italic* CO<sub>2</sub>** & 50%"))
        self.assertIn(r"\textbf{bold \textit{italic} CO\textsubscript{2}}", tex)
        self.assertIn(r"\& 50\%", tex)
        self.assertIn(r"\textbackslash{}input", latex_richtext(normalize_richtext(r"\input{bad}")))

    def test_markup_does_not_change_scientific_content(self):
        source = "Ошибка грамматики. Благодарность. РНФ 25-74-00001. [1] CO2 107."
        self.assertEqual(richtext_plain(normalize_richtext(source)), source)

    def test_missing_values(self):
        for missing in (None, "", " ", "-", "–", "—"):
            with self.subTest(missing=missing):
                value = normalize(raw(abstract=missing, description=missing))
                self.assertIsNone(value.abstract)
                self.assertEqual(value.keywords, [])
                self.assertIn(QAIssue("WARNING", "missing_abstract", ""), issues(value))
                self.assertFalse(validate_submissions([value])[0].errors)

    def test_marked_up_missing_and_nonconsecutive_reference_numbers(self):
        self.assertIsNone(normalize_richtext("<p><em>—</em></p>"))
        source = "Литература:\n\n1. Первый источник.\n\n3. Третий источник."
        self.assertEqual(richtext_plain(normalize_richtext(source)), source)


class AuthorTests(unittest.TestCase):
    def test_alias_dedup_before_ids_and_no_single_markers(self):
        value = normalize(with_coauthors("Петров П.П., МГУ им. М.В. Ломоносова"))
        self.assertEqual(value.affiliations, {1: MSU})
        self.assertEqual([a.affiliations for a in value.authors], [[1], [1]])
        tex, display = render_latex([value], CONFIG, ROOT)
        self.assertNotIn(r"Иванов И.И.\textsuperscript", tex)
        self.assertFalse(display.sections[0].submissions[0].authors[0].affiliation_indices)

    def test_multiple_affiliations_and_real_superscripts(self):
        value = normalize(
            with_coauthors("Петров П.П., МГУ им. М.В. Ломоносова + Институт географии РАН")
        )
        self.assertEqual(value.authors[1].affiliations, [1, 2])
        self.assertEqual(value.affiliations, {1: MSU, 2: "Институт географии РАН"})
        tex, _ = render_latex([value], CONFIG, ROOT)
        self.assertIn(r"Иванов И.И.\textsuperscript{1}", tex)
        self.assertIn(r"Петров П.П.\textsuperscript{1,2}", tex)

    def test_known_comma_separated_affiliations(self):
        value = normalize(
            raw(
                speakers=[
                    {
                        "name": "Иванов И.И.",
                        "biography": (
                            "ФИЦ «Почвенный институт имени В.В. Докучаева», "
                            "МГУ имени М.В. Ломоносова"
                        ),
                    }
                ]
            )
        )
        self.assertEqual(value.authors[0].affiliations, [1, 2])
        self.assertEqual(value.affiliations[2], MSU)

    def test_no_fuzzy_institution_or_department_matching(self):
        self.assertNotEqual(REGISTRY.canonicalize("МГУ"), MSU)
        department = "Факультет почвоведения МГУ имени М.В. Ломоносова"
        self.assertEqual(REGISTRY.canonicalize(department), department)
        org = "ФГБНУ «Центр агроэкологии, комплексных мелиораций», г. Волгоград"
        self.assertEqual(REGISTRY.split(org), [org])

    def test_unicode_punctuation_registry_variants(self):
        self.assertEqual(REGISTRY.canonicalize("МГУ имени М.В.Ломоносова"), MSU)
        self.assertEqual(REGISTRY.canonicalize("  МГУ\u00a0им. М.В. Ломоносова "), MSU)
        with self.assertRaises(ValueError):
            AffiliationRegistry(
                [{"canonical": "A", "aliases": ["X"]}, {"canonical": "B", "aliases": ["X"]}]
            )

    def test_person_name_order_and_punctuation(self):
        for source, expected in [
            ("С.А. Яковлева", "Яковлева С.А."),
            ("К.Д. Шепина", "Шепина К.Д."),
            ("Никитин Д. А", "Никитин Д.А."),
        ]:
            self.assertEqual(normalize_person_name(source), (expected, True))
        self.assertEqual(normalize_person_name("Иванов И"), ("Иванов И", False))

    def test_dedup_merges_affiliations_and_preserves_speaker_email(self):
        value = normalize(
            with_coauthors(
                "Д.А. Никитин, ИГ РАН; Петров П.П., ИГРАН",
                speakers=[
                    {
                        "name": "Никитин Дмитрий Алексеевич",
                        "biography": MSU,
                        "email": "real@institution.ru",
                    }
                ],
            )
        )
        self.assertEqual(len(value.authors), 2)
        self.assertEqual(value.authors[0].name, "Никитин Д.А.")
        self.assertEqual(value.authors[0].affiliations, [1, 2])
        self.assertEqual(value.authors[0].email, "real@institution.ru")
        self.assertEqual(value.authors[0].role, "speaker")
        self.assertIn("duplicate_author", [i.type for i in issues(value)])

    def test_no_fuzzy_person_matching(self):
        value = normalize(with_coauthors("Иванов И.А., МГУ им. М.В. Ломоносова"))
        self.assertEqual(len(value.authors), 2)

    def test_coauthor_separator_and_provenance(self):
        answer = "Петров П.П., Организация, Москва; Сидоров С.С., Организация, Казань;"
        value = normalize(with_coauthors(answer))
        self.assertEqual(len(value.authors), 3)
        self.assertEqual(value.source.coauthor_answer, answer)
        self.assertIn("Организация, Москва", value.affiliations.values())

    def test_ambiguous_coauthors_are_reported_without_guessing(self):
        authors, diagnostics = parse_coauthors("Петров П.П., Сидоров С.С., Институт")
        self.assertEqual(authors, [])
        self.assertIn("unparsed_coauthor", [i.type for i in diagnostics])
        self.assertTrue(any(i.severity == "ERROR" for i in diagnostics))
        authors, diagnostics = parse_coauthors("неразобранная строка")
        self.assertFalse(authors)
        self.assertEqual(diagnostics[0].severity, "WARNING")

    def test_indexed_coauthors_including_multiple_affiliations(self):
        value = normalize(
            with_coauthors("Петров П.П.1,2, Сидоров С.С.2\n1 МГУ им. М.В. Ломоносова\n2 ИГ РАН")
        )
        self.assertEqual([a.affiliations for a in value.authors], [[1], [1, 2], [2]])
        self.assertFalse(validate_submissions([value])[0].errors)
        _, diagnostics = parse_coauthors("Петров П.П.3\n1 МГУ")
        self.assertEqual(diagnostics[0].severity, "ERROR")

    def test_affiliation_leak_markers_case_insensitive(self):
        for marker in (
            "ФГБНУ",
            "ФГБУ",
            "ФИЦ",
            "РАН",
            "МГУ",
            "университет",
            "институт",
            "центр",
            "лаборатория",
            "кафедра",
            "факультет",
            "ООО",
            "АО",
        ):
            value = normalize(raw(speakers=[{"name": f"Фильцов Р.Н. {marker.lower()}"}]))
            self.assertIn("suspicious_author_affiliation_leak", [i.type for i in issues(value)])
        organization = "Научный Центр Почвоведения"
        self.assertEqual(normalize_person_name(organization), (organization, False))

    def test_plenary_affiliation_exception(self):
        value = normalize(
            with_coauthors(
                "Институт географии РАН",
                submission_type={"name": "Пленарный доклад"},
                speakers=[{"name": "Иванов И.И."}],
            )
        )
        self.assertEqual(value.affiliations, {1: "Институт географии РАН"})
        self.assertFalse(any(i.type == "unparsed_coauthor" for i in issues(value)))


class KeywordAndQATests(unittest.TestCase):
    def test_keyword_normalization_and_protected_cases(self):
        self.assertEqual(
            normalize_keywords("Почвенный  микробиом; технология no-till. , ; -"),
            ["почвенный микробиом", "технология no-till"],
        )
        protected = "ДНК, pH, Арктика, Bacillus subtilis, CO2, РНК, МГУ"
        self.assertEqual(normalize_keywords(protected), protected.split(", "))
        self.assertEqual(
            normalize_keywords("Почвенный институт им. В.В. Докучаева"),
            ["Почвенный институт им. В.В. Докучаева"],
        )
        self.assertEqual(
            normalize_keywords("микробная <em>активность</em>"), ["микробная активность"]
        )

    def test_placeholder_email_is_retained_and_warned(self):
        value = normalize(raw(speakers=[{"name": "Иванов И.И.", "email": "test@test.ru"}]))
        self.assertEqual(value.authors[0].email, "test@test.ru")
        self.assertIn("placeholder_email", [i.type for i in issues(value)])

    def test_bibliography_diagnostics_only(self):
        for source, expected in [
            (
                "Текст.\n\nЛитература:\n\n1. Иванов. 2020.",
                "references_present_but_no_intext_citations",
            ),
            (
                "Текст [1], (Иванов, 2020).\n\nЛитература:\n\n1. Иванов. 2020.",
                "mixed_citation_style",
            ),
        ]:
            value = normalize(raw(abstract=source))
            self.assertIn(expected, [i.type for i in issues(value)])
            self.assertEqual(richtext_plain(value.abstract), source)

    def test_structural_errors(self):
        value = normalize(raw())
        value.authors[0].affiliations = [999]
        self.assertIn("invalid_affiliation_reference", [i.type for i in issues(value)])
        value.authors[0].name = ""
        self.assertIn("author_without_name", [i.type for i in issues(value)])
        value.abstract = {
            "format": "richtext",
            "blocks": [{"type": "paragraph", "children": [{"type": "paragraph", "children": []}]}],
        }
        self.assertIn("invalid_richtext", [i.type for i in issues(value)])

    def test_build_gate_only_blocks_errors(self):
        warning_records = validate_submissions([normalize(raw(abstract="—"))])
        with patch("esoil_proceedings.cli.run_validate", return_value=warning_records):
            _check_build_validation(force=False)
        bad = normalize(raw())
        bad.authors[0].affiliations = [999]
        with (
            patch("esoil_proceedings.cli.run_validate", return_value=validate_submissions([bad])),
            self.assertRaises(BuildError),
        ):
            _check_build_validation(force=False)

    def test_qa_reports_and_yaml_round_trip(self):
        value = normalize(with_coauthors("Петров П.П., МГУ им. М.В. Ломоносова"))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "proceedings.yaml"
            write_proceedings(path, "pgb2026", ("confirmed",), ("Гласный доклад",), [value])
            self.assertEqual(read_proceedings(path)[0].to_dict(), value.to_dict())
            write_qa_reports(directory, validate_submissions([value]))
            report = json.loads((directory / "qa-report.json").read_text(encoding="utf8"))
            self.assertIn("TEST", report["submissions"])
            self.assertEqual(report["submissions"]["TEST"][0]["severity"], "INFO")
            self.assertTrue((directory / "qa-report.txt").exists())

    def test_legacy_schema_requires_explicit_renormalization(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "old.yaml"
            path.write_text("schema_version: 1\nsubmissions: []\n", encoding="utf8")
            with self.assertRaisesRegex(ValueError, "rerun normalize"):
                read_proceedings(path)


class ProgramRegressionTests(unittest.TestCase):
    def test_filters(self):
        samples = [raw(), raw(state="accepted"), raw(submission_type={"name": "Слушатель"})]
        result = normalize_submissions(samples, ["confirmed"], ["Гласный доклад"])
        self.assertEqual(len(result), 1)

    def test_program_order_plenary_groups_and_presentation(self):
        samples = [
            normalize(raw(code="BIO")),
            normalize(raw(code="FERT", track={"id": 28})),
            normalize(raw(code="PLBIO", submission_type={"name": "Пленарный доклад"})),
            normalize(
                raw(code="PLFERT", track={"id": 28}, submission_type={"name": "Пленарный доклад"})
            ),
        ]
        config = load_render_config(CONFIG, ROOT)
        display = build_display_model(
            samples, config.program_sections, config.pretalx_section_mapping
        )
        self.assertEqual([s.key for s in display.sections], ["fertility", "biota"])
        self.assertEqual([s.key for s in display.plenary.groups], ["fertility", "biota"])
        tex, _ = render_latex(samples, CONFIG, ROOT)
        for text in (
            "section:biota",
            "contents:start",
            "contents:end",
            "И.О. ФамилияОдин",
            "И.О. ФамилияДва",
            "И.О. ФамилияТри",
            "akron.jpg",
            "econiva.jpg",
        ):
            self.assertIn(text, tex)

    def test_config_loads_normalization_registry(self):
        with patch.dict(
            os.environ, {"PRETALX_BASE_URL": "https://conf.esoil.ru", "PRETALX_EVENT": "pgb2026"}
        ):
            config = load_config(CONFIG)
        self.assertEqual(config.affiliation_registry.canonicalize("МГУ имени М.В. Ломоносова"), MSU)


if __name__ == "__main__":
    unittest.main()
