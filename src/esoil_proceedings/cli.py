from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import ConferenceConfig, ConfigError, load_config
from .models import Submission, ValidationRecord
from .normalize import normalize_submissions
from .pretalx import PretalxClient, PretalxError
from .storage import (
    read_proceedings,
    read_raw,
    write_proceedings,
    write_raw,
    write_validation,
    write_validation_report,
)
from .validate import validate_submissions

RAW_PATH = Path("data/raw/submissions.json")
SPEAKERS_PATH = Path("data/raw/speakers.json")
PROCEEDINGS_PATH = Path("data/proceedings.yaml")
VALIDATION_PATH = Path("build/validation.csv")
VALIDATION_REPORT_PATH = Path("build/validation.md")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="esoil-proceedings",
        description="Fetch and validate conference abstracts from Pretalx.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/conference.yaml"),
        help="conference configuration (default: config/conference.yaml)",
    )
    parser.add_argument("command", choices=("fetch", "normalize", "validate", "all"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        load_dotenv(dotenv_path=Path(".env"), override=False)
        config = load_config(args.config)
        if args.command in {"fetch", "all"}:
            run_fetch(config, announce=args.command != "all")
        if args.command in {"normalize", "all"}:
            run_normalize(config, announce=args.command != "all")
        if args.command in {"validate", "all"}:
            run_validate()
    except (ConfigError, PretalxError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


def run_fetch(config: ConferenceConfig, *, announce: bool = True) -> list[dict[str, object]]:
    client = PretalxClient(config.base_url, config.event)
    submissions = client.fetch_submissions()
    speakers = client.fetch_speakers()
    write_raw(RAW_PATH, submissions)
    write_raw(SPEAKERS_PATH, speakers)
    if announce:
        print(f"Submissions fetched: {len(submissions)}")
    return submissions


def run_normalize(config: ConferenceConfig, *, announce: bool = True) -> list[Submission]:
    raw_submissions = read_raw(RAW_PATH)
    try:
        raw_speakers = read_raw(SPEAKERS_PATH)
    except ValueError:
        raw_speakers = []
    speaker_emails = {
        str(speaker.get("code", "")): str(speaker["email"])
        for speaker in raw_speakers
        if speaker.get("code") and speaker.get("email")
    }
    submissions = normalize_submissions(
        raw_submissions,
        config.include_states,
        config.include_submission_types,
        speaker_emails,
    )
    write_proceedings(
        PROCEEDINGS_PATH,
        config.event,
        config.include_states,
        config.include_submission_types,
        submissions,
    )
    if announce:
        print(f"Included: {len(submissions)}")
    return submissions


def run_validate() -> list[ValidationRecord]:
    submissions = read_proceedings(PROCEEDINGS_PATH)
    records = validate_submissions(submissions)
    write_validation(VALIDATION_PATH, records)
    write_validation_report(VALIDATION_REPORT_PATH, records)
    try:
        fetched = len(read_raw(RAW_PATH))
    except ValueError:
        fetched = len(submissions)
    valid = sum(record.status == "valid" for record in records)
    warnings = sum(record.status == "warning" for record in records)
    errors = sum(record.status == "error" for record in records)
    print(f"Submissions fetched: {fetched}")
    print(f"Included: {len(submissions)}")
    print(f"Valid: {valid}")
    print(f"Warnings: {warnings}")
    print(f"Errors: {errors}")
    return records


if __name__ == "__main__":
    raise SystemExit(main())
