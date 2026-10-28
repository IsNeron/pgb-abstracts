"""Convert source markup to a small semantic tree, never to stored LaTeX.

CommonMark handles Markdown delimiters; HTMLParser preserves inline whitespace.
Only explicit scientific markup (^7, _2, ^{...}, _{...}) is interpreted.
Bare numbers and chemical formulae are never inferred.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from markdown_it import MarkdownIt

MISSING_VALUES = {"", "-", "–", "—"}
INLINE_TAGS = {
    "em": "italic",
    "i": "italic",
    "strong": "bold",
    "b": "bold",
    "sup": "superscript",
    "sub": "subscript",
    "code": "code",
}
BLOCK_TAGS = {"p", "div", "li", "blockquote", "h1", "h2", "h3", "h4", "pre"}
SCRIPT_MARKUP = re.compile(r"(?<=[\w)])([\^_])(?:\{([+−\-\d]+)\}|([+−\-]?\d+))")
MARKDOWN = MarkdownIt("commonmark", {"html": True})
# Reference numbering is editorial content. CommonMark lists would renumber even
# explicitly nonconsecutive source numbers, so retain those markers as plain text.
MARKDOWN.disable("list")


class SemanticParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict[str, Any]] = []
        self.nodes: list[dict[str, Any]] = []
        self.stack: list[tuple[str, list[dict[str, Any]]]] = []
        self.ignored: list[str] = []

    def flush(self) -> None:
        if self.stack:
            return
        if any(inline_plain(node).strip() for node in self.nodes):
            self.blocks.append({"type": "paragraph", "children": self.nodes})
        self.nodes = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"} or self.ignored:
            self.ignored.append(tag)
            return
        if tag in BLOCK_TAGS:
            self.flush()
        elif tag == "br":
            self.nodes.append({"type": "linebreak"})
        elif tag in INLINE_TAGS:
            child: dict[str, Any] = {"type": INLINE_TAGS[tag], "children": []}
            self.nodes.append(child)
            self.stack.append((tag, self.nodes))
            self.nodes = child["children"]
        elif tag == "img":
            self.handle_data(dict(attrs).get("alt") or "")

    def handle_endtag(self, tag: str) -> None:
        if self.ignored:
            if tag == self.ignored[-1]:
                self.ignored.pop()
            return
        if tag in INLINE_TAGS:
            # A mismatched HTML close must not lose the surrounding text.
            if any(open_tag == tag for open_tag, _ in self.stack):
                while self.stack:
                    open_tag, parent = self.stack.pop()
                    self.nodes = parent
                    if open_tag == tag:
                        break
        elif tag in BLOCK_TAGS:
            self.flush()

    def handle_data(self, data: str) -> None:
        if self.ignored:
            return
        data = re.sub(r"\s+", " ", data)
        if not data:
            return
        cursor = 0
        # Code and explicit HTML scripts are already semantic, not markup to reinterpret.
        matches = (
            ()
            if any(tag in {"code", "sup", "sub", "pre"} for tag, _ in self.stack)
            else SCRIPT_MARKUP.finditer(data)
        )
        for match in matches:
            if match.start() > cursor:
                self.nodes.append({"type": "text", "text": data[cursor : match.start()]})
            self.nodes.append(
                {
                    "type": "superscript" if match[1] == "^" else "subscript",
                    "children": [{"type": "text", "text": match[2] or match[3]}],
                }
            )
            cursor = match.end()
        if cursor < len(data):
            self.nodes.append({"type": "text", "text": data[cursor:]})

    def document(self) -> dict[str, Any] | None:
        while self.stack:
            _, self.nodes = self.stack.pop()
        self.flush()
        return {"format": "richtext", "blocks": self.blocks} if self.blocks else None


def normalize_richtext(value: str | None) -> dict[str, Any] | None:
    if value is None or value.strip() in MISSING_VALUES:
        return None
    source = value.replace("\r\n", "\n").replace("\r", "\n")
    parser = SemanticParser()
    parser.feed(MARKDOWN.render(source))
    parser.close()
    document = parser.document()
    return None if richtext_plain(document).strip() in MISSING_VALUES else document


def inline_plain(node: dict[str, Any]) -> str:
    if node["type"] == "text":
        return node["text"]
    if node["type"] == "linebreak":
        return "\n"
    return "".join(inline_plain(child) for child in node["children"])


def richtext_plain(value: dict[str, Any] | None) -> str:
    if not value:
        return ""
    return "\n\n".join(
        "".join(inline_plain(node) for node in block["children"]).strip()
        for block in value["blocks"]
    )


def plain_source(value: str | None) -> str:
    """Strip markup only for fields whose contract is plain text."""
    return richtext_plain(normalize_richtext(value))


def validate_richtext(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or value.get("format") != "richtext":
        raise ValueError("abstract must be null or a semantic richtext object (schema 2)")

    def check(node: Any) -> None:
        if not isinstance(node, dict):
            raise ValueError("richtext node must be an object")
        kind = node.get("type")
        if kind == "text":
            if not isinstance(node.get("text"), str):
                raise ValueError("richtext text node must contain text")
        elif kind == "linebreak":
            return
        elif isinstance(kind, str) and kind in INLINE_TAGS.values():
            if not isinstance(node.get("children"), list):
                raise ValueError("richtext children must be a list")
            for child in node["children"]:
                check(child)
        else:
            raise ValueError(f"unsupported richtext node: {kind}")

    if not isinstance(value.get("blocks"), list):
        raise ValueError("richtext blocks must be a list")
    for block in value["blocks"]:
        if not isinstance(block, dict) or block.get("type") != "paragraph":
            raise ValueError("richtext block must be a paragraph")
        if not isinstance(block.get("children"), list):
            raise ValueError("richtext paragraph children must be a list")
        for child in block["children"]:
            check(child)
