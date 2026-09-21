from __future__ import annotations

from copy import deepcopy

from esoil_proceedings.normalize import (
    normalize_submission,
    normalize_submissions,
    parse_coauthors,
    parse_keywords,
)


def test_parse_multiple_coauthors_and_first_comma_only() -> None:
    authors, warnings = parse_coauthors(
        "Иванов И.И., МГУ; Петров П.П., НИУ ВШЭ, факультет наук"
    )

    assert warnings == []
    assert [author.name for author in authors] == ["Иванов И.И.", "Петров П.П."]
    assert authors[1].affiliation == "НИУ ВШЭ, факультет наук"


def test_dash_and_no_mean_no_coauthors() -> None:
    for value in ("-", "—", "нет", "Нет", " НЕТ "):
        assert parse_coauthors(value) == ([], [])


def test_ambiguous_coauthor_is_not_guessed() -> None:
    authors, warnings = parse_coauthors("Иванов И.И. МГУ")

    assert authors == []
    assert warnings == ["coauthor item 1 cannot be parsed reliably: Иванов И.И. МГУ"]


def test_suspicious_initial_punctuation_is_not_mistaken_for_separator() -> None:
    authors, warnings = parse_coauthors("Юдина А,В. Почвенный институт")

    assert authors == []
    assert "suspicious name punctuation" in warnings[0]
    assert "Юдина А,В. Почвенный институт" in warnings[0]


def test_keywords_are_trimmed_without_other_changes() -> None:
    assert parse_keywords(" Почвы, КТ , , pH ") == ["Почвы", "КТ", "pH"]


def test_trailing_semicolon_does_not_create_a_phantom_coauthor() -> None:
    authors, warnings = parse_coauthors("Иванов И.И., МГУ;")

    assert len(authors) == 1
    assert warnings == []


def test_normalization_preserves_provenance(raw_submission: dict[str, object]) -> None:
    raw_submission["abstract"] = "Первая строка.\r\nВторая строка."
    normalized = normalize_submission(raw_submission)

    assert normalized.code == "37WVUJ"
    assert normalized.submission_type.id == 20
    assert normalized.submission_type.name == "Устный доклад"
    assert normalized.section.name == "1.5. Физика и гидрология почв"
    assert [author.role for author in normalized.authors] == ["speaker", "coauthor", "coauthor"]
    assert normalized.authors[0].email == "speaker@example.org"
    assert normalized.authors[1].email is None
    assert normalized.authors[2].affiliation == "МГУ, факультет почвоведения"
    assert normalized.source.coauthor_answer == raw_submission["answers"][0]["answer"]
    assert normalized.source.coauthor_question_identifier == "CDHXWQFZ"
    assert normalized.abstract == "Первая строка.\nВторая строка."


def test_plenary_organization_is_used_as_speaker_affiliation(
    raw_submission: dict[str, object],
) -> None:
    raw_submission["submission_type"] = {"id": 23, "name": {"ru": "Пленарный доклад"}}
    raw_submission["speakers"][0]["biography"] = None
    raw_submission["answers"][0]["answer"] = "ФИЦ «Почвенный институт им. В.В. Докучаева»"

    normalized = normalize_submission(raw_submission)

    assert len(normalized.authors) == 1
    assert normalized.authors[0].affiliation == "ФИЦ «Почвенный институт им. В.В. Докучаева»"
    assert normalized.normalization_warnings == []
    assert normalized.source.coauthor_answer == "ФИЦ «Почвенный институт им. В.В. Докучаева»"


def test_plenary_affiliation_rule_does_not_guess_a_coauthor_list(
    raw_submission: dict[str, object],
) -> None:
    raw_submission["submission_type"] = {"id": 23, "name": {"ru": "Пленарный доклад"}}
    raw_submission["speakers"][0]["biography"] = None
    raw_submission["answers"][0]["answer"] = "Иванов И.И., МГУ"

    normalized = normalize_submission(raw_submission)

    assert normalized.authors[0].affiliation == ""
    assert normalized.authors[1].name == "Иванов И.И."


def test_confirmed_filtering_and_track_sorting(raw_submission: dict[str, object]) -> None:
    rejected = deepcopy(raw_submission)
    rejected["code"] = "REJECTED"
    rejected["state"] = "rejected"
    earlier = deepcopy(raw_submission)
    earlier["code"] = "EARLY"
    earlier["track"]["position"] = 1

    result = normalize_submissions([raw_submission, rejected, earlier], ["confirmed"])

    assert [submission.code for submission in result] == ["EARLY", "37WVUJ"]


def test_submission_type_filter_excludes_listeners_and_program_items(
    raw_submission: dict[str, object],
) -> None:
    listener = deepcopy(raw_submission)
    listener["code"] = "LISTENER"
    listener["submission_type"] = {"id": 100, "name": {"ru": "Слушатель"}}
    workshop = deepcopy(raw_submission)
    workshop["code"] = "WORKSHOP"
    workshop["submission_type"] = {"id": 101, "name": {"ru": "Мастер-класс"}}

    result = normalize_submissions(
        [raw_submission, listener, workshop],
        ["confirmed"],
        ["Устный доклад"],
    )

    assert [submission.code for submission in result] == ["37WVUJ"]


def test_speaker_email_can_be_joined_by_code(raw_submission: dict[str, object]) -> None:
    raw_submission["speakers"][0].pop("email")

    result = normalize_submissions(
        [raw_submission],
        ["confirmed"],
        speaker_emails={"HYTFXX": "joined@example.org"},
    )

    assert result[0].authors[0].email == "joined@example.org"
