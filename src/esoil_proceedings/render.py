from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from .models import Submission
from .richtext import validate_richtext
from .storage import read_proceedings


class BuildError(RuntimeError):
    """The proceedings source or PDF could not be built."""


@dataclass(frozen=True, slots=True)
class ConferenceMetadata:
    year: int
    edition: str
    title: str
    full_title: str
    organization: str
    city: str
    dates: str


@dataclass(frozen=True, slots=True)
class PublicationMetadata:
    udc: str
    bbk: str
    editors: tuple[str, ...]
    compilers: tuple[str, ...]
    publisher: str
    bibliographic_description: str
    description: str
    copyright: str


@dataclass(frozen=True, slots=True)
class SponsorAsset:
    path: str
    label: str


@dataclass(frozen=True, slots=True)
class SponsorMetadata:
    official: tuple[SponsorAsset, ...]
    partner: SponsorAsset | None

    @property
    def present(self) -> bool:
        return bool(self.official or self.partner)


@dataclass(frozen=True, slots=True)
class ProgramSection:
    key: str
    order: int
    title: str
    subtitle: str | None

    @property
    def is_masterclasses(self) -> bool:
        return self.key == "masterclasses"

    @property
    def kicker(self) -> str:
        return "" if self.is_masterclasses else f"Секция {self.order}"

    @property
    def toc_heading(self) -> str:
        if self.is_masterclasses:
            return self.title
        return f"Секция {self.order}. {self.title}"


@dataclass(frozen=True, slots=True)
class RenderConfig:
    conference: ConferenceMetadata
    publication: PublicationMetadata
    background_path: str
    logo_path: str
    sponsors: SponsorMetadata
    program_sections: tuple[ProgramSection, ...]
    pretalx_section_mapping: dict[str, str]


@dataclass(frozen=True, slots=True)
class DisplayAuthor:
    name: str
    display_name: str
    affiliation_indices: tuple[int, ...]
    role: str
    email: str | None


@dataclass(frozen=True, slots=True)
class DisplaySubmission:
    code: str
    label: str
    title: str
    authors: tuple[DisplayAuthor, ...]
    authors_text: str
    affiliations: tuple[tuple[int, str], ...]
    email: str | None
    keywords: tuple[str, ...]
    abstract: dict[str, Any] | None
    submission_type: str


@dataclass(frozen=True, slots=True)
class DisplaySection:
    key: str
    order: int
    title: str
    subtitle: str | None
    label: str
    submissions: tuple[DisplaySubmission, ...]
    is_plenary: bool = False

    @property
    def is_masterclasses(self) -> bool:
        return self.key == "masterclasses"

    @property
    def kicker(self) -> str:
        return "" if self.is_masterclasses else f"Секция {self.order}"

    @property
    def toc_heading(self) -> str:
        if self.is_masterclasses:
            return self.title
        return f"Секция {self.order}. {self.title}"


@dataclass(frozen=True, slots=True)
class DisplayPlenary:
    title: str
    label: str
    groups: tuple[DisplaySection, ...]

    @property
    def kicker(self) -> str:
        return ""

    @property
    def subtitle(self) -> None:
        return None

    @property
    def submissions(self) -> tuple[DisplaySubmission, ...]:
        return tuple(submission for group in self.groups for submission in group.submissions)


@dataclass(frozen=True, slots=True)
class DisplayProceedings:
    plenary: DisplayPlenary | None
    sections: tuple[DisplaySection, ...]


@dataclass(frozen=True, slots=True)
class BuildResult:
    tex_path: Path
    pdf_path: Path
    log_path: Path
    page_labels: dict[str, int]
    plenary: DisplayPlenary | None
    sections: tuple[DisplaySection, ...]
    total_submissions: int
    page_count: int


LATEX_REPLACEMENTS = {
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    "\\": r"\textbackslash{}",
}
URL_PATTERN = re.compile(r"https?://[^\s]+")
URL_TRAILING_PUNCTUATION = ".,;:!?"
PLENARY_TYPE = "Пленарный доклад"


def latex_escape(value: object) -> str:
    text = str(value)
    pieces: list[str] = []
    cursor = 0
    for match in URL_PATTERN.finditer(text):
        pieces.append(_escape_characters(text[cursor : match.start()]))
        raw_url = match.group()
        url = raw_url.rstrip(URL_TRAILING_PUNCTUATION)
        trailing = raw_url[len(url) :]
        url = url.replace("{", "%7B").replace("}", "%7D")
        pieces.append(r"\url{" + url + "}")
        pieces.append(_escape_characters(trailing))
        cursor = match.end()
    pieces.append(_escape_characters(text[cursor:]))
    return "".join(pieces)


def latex_title(value: object) -> str:
    escaped = latex_escape(value)
    return re.sub(r"\s+(\d{4})$", r"~\1", escaped)


def mailto_target(value: object) -> str:
    return str(value).replace("\\", "").replace("{", "").replace("}", "")


def _escape_characters(value: str) -> str:
    return "".join(LATEX_REPLACEMENTS.get(character, character) for character in value)


def latex_richtext(value: dict[str, Any] | None) -> str:
    validate_richtext(value)
    if value is None:
        return ""
    commands = {
        "italic": r"\textit",
        "bold": r"\textbf",
        "superscript": r"\textsuperscript",
        "subscript": r"\textsubscript",
        "code": r"\texttt",
    }

    def inline(node: dict[str, Any]) -> str:
        kind = node["type"]
        if kind == "text":
            return latex_escape(node["text"])
        if kind == "linebreak":
            return r"\newline{}"
        return commands[kind] + "{" + "".join(inline(x) for x in node["children"]) + "}"

    return "\n\n\\par\n\n".join(
        "".join(inline(x) for x in block["children"]) for block in value["blocks"]
    )


def latex_paragraphs(value: object) -> str:
    """Plain publication metadata; abstracts always use the semantic tree."""
    return "\n\n\\par\n\n".join(
        latex_escape(part.strip()) for part in str(value).split("\n\n") if part.strip()
    )


def build_display_model(
    submissions: list[Submission],
    program_sections: tuple[ProgramSection, ...],
    section_mapping: dict[str, str],
) -> DisplayProceedings:
    unknown = [
        submission
        for submission in submissions
        if str(submission.section.id) not in section_mapping
    ]
    if unknown:
        details = "; ".join(
            f"{submission.code}: {submission.section.name!r} (id={submission.section.id!r})"
            for submission in unknown
        )
        raise BuildError(
            f"Unmapped Pretalx sections; add them to pretalx_section_mapping: {details}"
        )

    plenary_grouped: dict[str, list[DisplaySubmission]] = {
        section.key: [] for section in program_sections
    }
    regular_grouped: dict[str, list[DisplaySubmission]] = {
        section.key: [] for section in program_sections
    }
    for submission in submissions:
        displayed = _display_submission(submission)
        section_key = section_mapping[str(submission.section.id)]
        if submission.submission_type.name == PLENARY_TYPE:
            plenary_grouped[section_key].append(displayed)
        else:
            regular_grouped[section_key].append(displayed)

    plenary = None
    plenary_groups = tuple(
        _display_section(section, plenary_grouped[section.key], plenary=True)
        for section in program_sections
        if plenary_grouped[section.key]
    )
    if plenary_groups:
        plenary = DisplayPlenary(
            title="Пленарные доклады",
            label="section:plenary",
            groups=plenary_groups,
        )
    sections = tuple(
        _display_section(section, regular_grouped[section.key])
        for section in program_sections
        if regular_grouped[section.key]
    )
    return DisplayProceedings(
        plenary=plenary,
        sections=sections,
    )


def _display_section(
    section: ProgramSection,
    submissions: list[DisplaySubmission],
    *,
    plenary: bool = False,
) -> DisplaySection:
    label_prefix = "plenary-group" if plenary else "section"
    return DisplaySection(
        key=section.key,
        order=section.order,
        title=section.title,
        subtitle=section.subtitle,
        label=f"{label_prefix}:{section.key}",
        submissions=tuple(submissions),
        is_plenary=plenary,
    )


def _display_submission(submission: Submission) -> DisplaySubmission:
    show_marks = len(submission.affiliations) > 1
    display_authors = tuple(
        DisplayAuthor(
            name=author.name,
            display_name=author.name,
            affiliation_indices=tuple(author.affiliations) if show_marks else (),
            role=author.role,
            email=author.email,
        )
        for author in submission.authors
    )
    return DisplaySubmission(
        code=submission.code,
        label=f"submission:{_safe_label(submission.code)}",
        title=submission.title,
        authors=display_authors,
        authors_text=", ".join(author.name for author in submission.authors),
        affiliations=tuple(submission.affiliations.items()),
        email=next((a.email for a in submission.authors if a.role == "speaker" and a.email), None),
        keywords=tuple(submission.keywords),
        abstract=submission.abstract,
        submission_type=submission.submission_type.name,
    )


def _safe_label(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", value)
    return safe or "unknown"


def load_render_config(path: Path, project_root: Path) -> RenderConfig:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise BuildError(f"Cannot read configuration {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise BuildError(f"Invalid YAML in {path}: {exc}") from exc
    conference = _mapping(document.get("conference"), "conference")
    publication = _mapping(document.get("publication"), "publication")
    assets = _mapping(document.get("assets"), "assets")
    program = _mapping(document.get("program"), "program")
    program_sections = _load_program_sections(program.get("sections"))
    section_mapping = _load_section_mapping(
        document.get("pretalx_section_mapping"), program_sections
    )
    year = conference.get("year")
    if not isinstance(year, int):
        raise BuildError("conference.year must be an integer")
    background = _asset_path(project_root, assets.get("background"), "assets.background")
    logo = _asset_path(project_root, assets.get("logo"), "assets.logo")
    organization = _string(conference.get("organization"), "conference.organization")
    return RenderConfig(
        conference=ConferenceMetadata(
            year=year,
            edition=_string(conference.get("edition"), "conference.edition"),
            title=_string(conference.get("title"), "conference.title"),
            full_title=_string(conference.get("full_title"), "conference.full_title"),
            organization=organization,
            city=_string(conference.get("city"), "conference.city"),
            dates=_string(conference.get("dates"), "conference.dates"),
        ),
        publication=PublicationMetadata(
            udc=_string(publication.get("udc"), "publication.udc"),
            bbk=_string(publication.get("bbk"), "publication.bbk"),
            editors=_string_list(publication.get("editors", []), "publication.editors"),
            compilers=_string_list(publication.get("compilers", []), "publication.compilers"),
            publisher=_string(publication.get("publisher"), "publication.publisher"),
            bibliographic_description=_string(
                publication.get("bibliographic_description"),
                "publication.bibliographic_description",
            ),
            description=_string(publication.get("description"), "publication.description"),
            copyright=_string(publication.get("copyright", organization), "publication.copyright"),
        ),
        background_path=background,
        logo_path=logo,
        sponsors=_load_sponsors(assets.get("sponsors"), project_root),
        program_sections=program_sections,
        pretalx_section_mapping=section_mapping,
    )


def render_latex(
    submissions: list[Submission], config_path: Path, project_root: Path
) -> tuple[str, DisplayProceedings]:
    if not submissions:
        raise BuildError("No submissions found in data/proceedings.yaml")
    config = load_render_config(config_path, project_root)
    proceedings = build_display_model(
        submissions,
        config.program_sections,
        config.pretalx_section_mapping,
    )
    template_dir = project_root / "templates"
    environment = Environment(
        loader=FileSystemLoader(template_dir),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    environment.filters["tex"] = latex_escape
    environment.filters["tex_title"] = latex_title
    environment.filters["tex_richtext"] = latex_richtext
    environment.filters["tex_paragraphs"] = latex_paragraphs
    environment.filters["mailto"] = mailto_target
    try:
        rendered = environment.get_template("main.tex.j2").render(
            conference=config.conference,
            publication=config.publication,
            background_path=config.background_path,
            logo_path=config.logo_path,
            sponsors=config.sponsors,
            plenary=proceedings.plenary,
            sections=proceedings.sections,
        )
    except TemplateError as exc:
        raise BuildError(f"Cannot render LaTeX templates: {exc}") from exc
    return rendered, proceedings


def build_pdf(config_path: Path, project_root: Path | None = None) -> BuildResult:
    root = (project_root or Path.cwd()).resolve()
    latexmk = _tool("latexmk")
    if not shutil.which("lualatex"):
        raise BuildError(
            "LuaLaTeX toolchain not found. Install TeX Live/MiKTeX with LuaLaTeX and latexmk."
        )
    build_dir = root / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    log_path = build_dir / "build.log"
    proceedings_path = root / "data/proceedings.yaml"
    submissions = read_proceedings(proceedings_path)
    try:
        tex, proceedings = render_latex(submissions, config_path.resolve(), root)
    except BuildError as exc:
        log_path.write_text(f"Configuration/render error: {exc}\n", encoding="utf-8")
        raise
    tex_path = build_dir / "proceedings.tex"
    pdf_path = build_dir / "proceedings.pdf"
    tex_path.write_text(tex, encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="latex-", dir=build_dir) as temporary:
        temporary_path = Path(temporary)
        command = [
            latexmk,
            "-lualatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            f"-outdir={temporary_path}",
            str(tex_path),
        ]
        process_environment = os.environ.copy()
        tex_cache = build_dir / ".tex-cache"
        tex_cache.mkdir(parents=True, exist_ok=True)
        process_environment["TEXMFVAR"] = str(tex_cache)
        result = subprocess.run(
            command,
            cwd=root,
            env=process_environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        latex_log = temporary_path / "proceedings.log"
        if latex_log.exists():
            output += "\n\n===== proceedings.log =====\n" + latex_log.read_text(
                encoding="utf-8", errors="replace"
            )
        log_path.write_text(output, encoding="utf-8")
        generated_pdf = temporary_path / "proceedings.pdf"
        if result.returncode or not generated_pdf.exists():
            raise BuildError("LuaLaTeX failed. See build/build.log for details.")
        shutil.copyfile(generated_pdf, pdf_path)
        page_labels = _read_page_labels(temporary_path / "proceedings.aux")
        (build_dir / "page-labels.json").write_text(
            json.dumps(page_labels, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    return BuildResult(
        tex_path=tex_path,
        pdf_path=pdf_path,
        log_path=log_path,
        page_labels=page_labels,
        plenary=proceedings.plenary,
        sections=proceedings.sections,
        total_submissions=len(submissions),
        page_count=page_labels.get("LastPage", 0),
    )


def render_preview(config_path: Path, project_root: Path | None = None) -> list[Path]:
    root = (project_root or Path.cwd()).resolve()
    renderer = _tool("pdftoppm", preview=True)
    result = build_pdf(config_path, root)
    all_submissions = list(result.plenary.submissions if result.plenary else ())
    all_submissions.extend(item for section in result.sections for item in section.submissions)
    requests: list[tuple[str, int]] = [
        ("cover", 1),
        ("imprint", 2),
        ("contents-first", result.page_labels.get("contents:start", 3)),
        ("contents-last", result.page_labels.get("contents:end", 3)),
    ]
    if result.plenary is not None:
        requests.append(
            (
                "plenary-divider",
                result.page_labels.get(result.plenary.label, 4),
            )
        )
        first_plenary = result.plenary.submissions[0]
        if first_plenary.label in result.page_labels:
            requests.append(("first-plenary-submission", result.page_labels[first_plenary.label]))
    if result.sections:
        first_section = result.sections[0]
        requests.append(
            (
                "first-normal-section-divider",
                result.page_labels.get(first_section.label, 4),
            )
        )
        first_regular = first_section.submissions[0]
        if first_regular.label in result.page_labels:
            requests.append(("first-normal-submission", result.page_labels[first_regular.label]))

    samples = (
        (
            "sample-multiple-authors",
            lambda item: len(item.authors) > 1,
        ),
        (
            "sample-multiple-affiliations",
            lambda item: len(item.affiliations) > 1,
        ),
    )
    selected_pages = {page for _, page in requests}
    for name, predicate in samples:
        submission = next(
            (
                item
                for item in all_submissions
                if predicate(item)
                and item.label in result.page_labels
                and result.page_labels[item.label] not in selected_pages
            ),
            None,
        )
        if submission is not None and submission.label in result.page_labels:
            page = result.page_labels[submission.label]
            requests.append((f"{name}-{submission.code}", page))
            selected_pages.add(page)
    long_title = max(all_submissions, key=lambda item: len(item.title), default=None)
    if long_title is not None and long_title.label in result.page_labels:
        page = result.page_labels[long_title.label]
        requests.append((f"sample-long-title-{long_title.code}", page))

    # Keep the editorial regression section reproducible, including continuation pages.
    biota = [
        s for section in result.sections if section.key == "biota" for s in section.submissions
    ]
    if result.plenary:
        biota.extend(
            s for group in result.plenary.groups if group.key == "biota" for s in group.submissions
        )
    boundaries = sorted(set(result.page_labels.values()))
    for submission in biota:
        start = result.page_labels.get(submission.label)
        if start is None:
            continue
        end = next((p for p in boundaries if p > start), result.page_count + 1)
        for page in range(start, end):
            requests.append((f"biota-{submission.code}-p{page}", page))

    preview_dir = root / "build/preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for old_preview in preview_dir.glob("*.png"):
        old_preview.unlink()
    pages_manifest = preview_dir / "pages.json"
    if pages_manifest.exists():
        pages_manifest.unlink()
    generated: list[Path] = []
    for number, (name, page) in enumerate(requests, start=1):
        output_base = preview_dir / f"{number:02d}-{name}"
        command = [
            renderer,
            "-f",
            str(page),
            "-l",
            str(page),
            "-png",
            "-r",
            "140",
            "-singlefile",
            str(result.pdf_path),
            str(output_base),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode:
            raise BuildError(f"PDF preview rendering failed: {completed.stderr.strip()}")
        generated.append(output_base.with_suffix(".png"))
    pages_manifest.write_text(
        json.dumps(
            [{"name": name, "page": page} for name, page in requests],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return generated


def _read_page_labels(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    labels: dict[str, int] = {}
    pattern = re.compile(r"\\newlabel\{([^}]+)\}\{\{[^}]*\}\{(\d+)\}")
    for label, page in pattern.findall(path.read_text(encoding="utf-8", errors="replace")):
        labels[label] = int(page)
    return labels


def _tool(name: str, *, preview: bool = False) -> str:
    executable = shutil.which(name)
    if executable:
        return executable
    if preview:
        raise BuildError("PDF preview tool not found. Install pdftoppm (Poppler).")
    raise BuildError(
        "LuaLaTeX toolchain not found. Install TeX Live/MiKTeX with LuaLaTeX and latexmk."
    )


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BuildError(f"{name} must be a mapping")
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BuildError(f"{name} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise BuildError(f"{name} must be a list")
    return tuple(_string(item, f"{name} item") for item in value)


def _load_program_sections(value: Any) -> tuple[ProgramSection, ...]:
    if not isinstance(value, list) or not value:
        raise BuildError("program.sections must be a non-empty list")
    sections: list[ProgramSection] = []
    keys: set[str] = set()
    orders: set[int] = set()
    for index, item in enumerate(value):
        name = f"program.sections[{index}]"
        section = _mapping(item, name)
        key = _string(section.get("key"), f"{name}.key")
        order = section.get("order")
        if not isinstance(order, int) or isinstance(order, bool) or order < 1:
            raise BuildError(f"{name}.order must be a positive integer")
        if key in keys:
            raise BuildError(f"Duplicate program section key: {key}")
        if order in orders:
            raise BuildError(f"Duplicate program section order: {order}")
        keys.add(key)
        orders.add(order)
        subtitle_value = section.get("subtitle")
        subtitle = None
        if subtitle_value is not None:
            subtitle = _string(subtitle_value, f"{name}.subtitle")
        sections.append(
            ProgramSection(
                key=key,
                order=order,
                title=_string(section.get("title"), f"{name}.title"),
                subtitle=subtitle,
            )
        )
    return tuple(sorted(sections, key=lambda section: section.order))


def _load_section_mapping(
    value: Any, program_sections: tuple[ProgramSection, ...]
) -> dict[str, str]:
    mapping = _mapping(value, "pretalx_section_mapping")
    known_keys = {section.key for section in program_sections}
    result: dict[str, str] = {}
    for raw_id, raw_key in mapping.items():
        section_id = str(raw_id).strip()
        key = _string(raw_key, f"pretalx_section_mapping[{section_id}]")
        if key not in known_keys:
            raise BuildError(
                f"pretalx_section_mapping[{section_id}] references unknown program key: {key}"
            )
        result[section_id] = key
    return result


def _load_sponsors(value: Any, project_root: Path) -> SponsorMetadata:
    if value is None:
        return SponsorMetadata(official=(), partner=None)
    sponsors = _mapping(value, "assets.sponsors")
    official_value = sponsors.get("official", [])
    if not isinstance(official_value, list):
        raise BuildError("assets.sponsors.official must be a list")
    official: list[SponsorAsset] = []
    for index, item in enumerate(official_value):
        name = f"assets.sponsors.official[{index}]"
        sponsor = _mapping(item, name)
        official.append(
            SponsorAsset(
                path=_asset_path(project_root, sponsor.get("path"), f"{name}.path"),
                label=_string(sponsor.get("label"), f"{name}.label"),
            )
        )
    partner_value = sponsors.get("partner")
    partner = None
    if partner_value is not None:
        sponsor = _mapping(partner_value, "assets.sponsors.partner")
        partner = SponsorAsset(
            path=_asset_path(
                project_root,
                sponsor.get("path"),
                "assets.sponsors.partner.path",
            ),
            label=_string(sponsor.get("label"), "assets.sponsors.partner.label"),
        )
    return SponsorMetadata(official=tuple(official), partner=partner)


def _asset_path(project_root: Path, value: Any, name: str) -> str:
    relative = Path(_string(value, name))
    if relative.is_absolute():
        raise BuildError(f"{name} must be a repository-relative path")
    path = (project_root / relative).resolve()
    if not path.is_relative_to(project_root.resolve()):
        raise BuildError(f"{name} points outside the project: {relative}")
    if not path.is_file():
        raise BuildError(f"Asset for {name} does not exist: {relative.as_posix()}")
    return relative.as_posix()
