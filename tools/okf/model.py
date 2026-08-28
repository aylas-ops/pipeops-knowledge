"""Parser and object model for OKF v0.2 knowledge bundles.

This module implements only the structural conventions of the specification:
frontmatter parsing, reserved filenames, links, footnotes, chunking, and the
derived trust/staleness signals defined in SPEC §5. It enforces no policy --
that lives in `okf.validate`, driven by `okf-profile.yaml`.

Spec reference: OKF v0.2, GoogleCloudPlatform/knowledge-catalog.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml

OKF_VERSION = "0.2"

# SPEC §3.1 -- reserved at every level of the hierarchy.
RESERVED_FILENAMES = frozenset({"index.md", "log.md"})

# SPEC §5.4
STATUS_VALUES = frozenset({"draft", "stable", "deprecated"})
DEFAULT_STATUS = "stable"

# SPEC §5.3
TRUST_UNVERIFIED = "unverified"
TRUST_MACHINE = "machine-confirmed"
TRUST_HUMAN = "human-reviewed"

# SPEC §7 -- `human:<id>`, `process:<id>`, `<producer>/<version>`. `team:<id>`
# is used by the reference bundles for `sources[].author`, so it is accepted.
ACTOR_PREFIXES = ("human:", "process:", "team:")
_ACTOR_SLASH = re.compile(r"\A[A-Za-z0-9._-]+/[A-Za-z0-9._+-]+\Z")

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_MD_LINK = re.compile(r"(?<!!)\[([^\]\n]*)\]\(\s*<?([^)>\s]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
_FOOTNOTE_REF = re.compile(r"\[\^([^\]\s]+)\]")
_FOOTNOTE_DEF = re.compile(r"^[ \t]{0,3}\[\^([^\]\s]+)\]:", re.MULTILINE)
_FENCE_OPEN = re.compile(r"\A(`{3,}|~{3,})")
_EXTERNAL_URI = re.compile(r"\A[a-zA-Z][a-zA-Z0-9+.-]*:")


# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------


def _blank_like(line: str) -> str:
    """Replace a line with spaces, preserving length and newlines."""
    return "".join("\n" if ch == "\n" else " " for ch in line)


def mask_fenced_code(text: str) -> str:
    """Blank out fenced code blocks while preserving every character offset.

    Lets heading/link/footnote scanning ignore markdown that only *looks*
    like structure because it lives inside a code fence.
    """
    out: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        match = _FENCE_OPEN.match(stripped)
        if fence is None:
            if match:
                fence = match.group(1)[0] * 3
                out.append(_blank_like(line))
                continue
            out.append(line)
        else:
            out.append(_blank_like(line))
            if match and match.group(1)[0] * 3 == fence:
                fence = None
    return "".join(out)


def line_of(text: str, offset: int) -> int:
    """1-indexed line number for a character offset."""
    return text.count("\n", 0, offset) + 1


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "untitled"


# --------------------------------------------------------------------------
# frontmatter
# --------------------------------------------------------------------------


class FrontmatterError(ValueError):
    """Raised when a frontmatter block is present but not parseable YAML."""


def split_frontmatter(raw: str) -> tuple[dict[str, Any] | None, str, int]:
    """Split a document into (frontmatter, body, body_start_line).

    Returns ``(None, raw, 1)`` when the document has no frontmatter block.
    Raises :class:`FrontmatterError` when the block is present but invalid, or
    parses to something other than a mapping.
    """
    match = _FRONTMATTER.match(raw)
    if not match:
        return None, raw, 1

    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:  # pragma: no cover - message varies by input
        raise FrontmatterError(str(exc).replace("\n", " ")) from exc

    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise FrontmatterError(
            f"frontmatter must be a YAML mapping, got {type(parsed).__name__}"
        )

    body = raw[match.end() :]
    return parsed, body, line_of(raw, match.end())


_YAML_KEYWORD = re.compile(r"\A(?:true|false|null|yes|no|on|off|~)\Z", re.I)
_NUMERIC = re.compile(r"\A[-+]?(?:\d[\d_]*(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\Z")
_TIMESTAMP_LIKE = re.compile(r"\A\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}.*)?\Z")
_INDICATOR_START = "-?:,[]{}#&*!|>'\"%@`"


def _needs_quotes(text: str, *, flow: bool) -> bool:
    """Whether a plain YAML scalar would be misread.

    Deliberately narrow: quoting a URL because it contains a colon produces
    frontmatter that is correct but noisy, and every generated file carries
    URLs. Only the constructs that actually change the parse are quoted.
    """
    if text == "" or text != text.strip():
        return True
    if text[0] in _INDICATOR_START:
        return True
    if ": " in text or text.endswith(":") or " #" in text:
        return True
    if _YAML_KEYWORD.match(text) or _NUMERIC.match(text):
        return True
    if flow and any(char in text for char in ",[]{}"):
        return True
    return False


def _scalar(value: Any, *, flow: bool = False) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dt.datetime):
        return format_timestamp(value)
    if isinstance(value, dt.date):
        return value.isoformat()

    text = str(value)
    # A timestamp string is round-tripped unquoted so YAML resolves it back to
    # a datetime, which is what the trust and lifecycle families expect.
    if _TIMESTAMP_LIKE.match(text):
        return text
    if _needs_quotes(text, flow=flow):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _flow(value: Any) -> str:
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k}: {_flow(v)}" for k, v in value.items()) + " }"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_flow(v) for v in value) + "]"
    return _scalar(value, flow=True)


def render_frontmatter(data: dict[str, Any]) -> str:
    """Render frontmatter in the block style used throughout the spec.

    Short mappings (`generated`, `usage_window`) and scalar lists (`tags`)
    render inline; `sources` and `parameters` render as block sequences.
    """
    lines = ["---"]
    for key, value in data.items():
        if value is None or value == [] or value == {}:
            continue
        if isinstance(value, dict):
            lines.append(f"{key}: {_flow(value)}")
        elif isinstance(value, list):
            if all(not isinstance(item, (dict, list)) for item in value):
                lines.append(f"{key}: {_flow(value)}")
            else:
                lines.append(f"{key}:")
                for item in value:
                    if isinstance(item, dict) and len(item) <= 3:
                        lines.append(f"  - {_flow(item)}")
                    elif isinstance(item, dict):
                        first = True
                        for sub_key, sub_value in item.items():
                            if sub_value is None:
                                continue
                            prefix = "  - " if first else "    "
                            lines.append(f"{prefix}{sub_key}: {_flow(sub_value)}")
                            first = False
                    else:
                        lines.append(f"  - {_flow(item)}")
        else:
            lines.append(f"{key}: {_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# timestamps (SPEC §5: ISO 8601 with an explicit UTC offset)
# --------------------------------------------------------------------------


def parse_timestamp(value: Any) -> dt.datetime:
    """Coerce a frontmatter value to a timezone-aware datetime.

    PyYAML already resolves ISO timestamps to ``datetime`` and bare dates to
    ``date``; both arrive here, as do plain strings. A value without an
    explicit offset raises, because the spec requires one.
    """
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, dt.date):
        raise ValueError("date without a time and UTC offset")
    elif isinstance(value, str):
        text = value.strip()
        try:
            parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"not an ISO 8601 datetime: {value!r}") from exc
    else:
        raise ValueError(f"not a timestamp: {value!r}")

    if parsed.tzinfo is None:
        raise ValueError("missing an explicit UTC offset")
    return parsed


def format_timestamp(value: dt.datetime) -> str:
    """Render as ``YYYY-MM-DDTHH:MM:SSZ`` when UTC, else with a numeric offset."""
    value = value.replace(microsecond=0)
    if value.utcoffset() == dt.timedelta(0):
        return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value.isoformat()


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def is_actor(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    if text.startswith(ACTOR_PREFIXES):
        return len(text.split(":", 1)[1]) > 0
    return bool(_ACTOR_SLASH.match(text))


# --------------------------------------------------------------------------
# concepts
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Link:
    text: str
    target: str
    line: int

    @property
    def is_external(self) -> bool:
        return bool(_EXTERNAL_URI.match(self.target))

    @property
    def is_anchor(self) -> bool:
        return self.target.startswith("#")

    @property
    def path_part(self) -> str:
        return self.target.split("#", 1)[0]


@dataclass(frozen=True)
class Chunk:
    """A retrieval unit: one heading's worth of body text.

    Frontmatter is re-attached at index time so metadata filters keep working
    on every chunk (manual §9).
    """

    heading: str
    level: int
    ordinal: int
    text: str


@dataclass
class Concept:
    """One markdown document inside a bundle."""

    bundle_root: Path
    path: Path
    raw: str
    frontmatter: dict[str, Any]
    body: str
    body_start_line: int
    parse_error: str | None = None

    # -- identity ----------------------------------------------------------

    @property
    def rel_path(self) -> str:
        return self.path.relative_to(self.bundle_root).as_posix()

    @property
    def concept_id(self) -> str:
        """SPEC §2 -- the bundle-relative path with the `.md` suffix removed."""
        return self.rel_path[:-3] if self.rel_path.endswith(".md") else self.rel_path

    @property
    def bundle_name(self) -> str:
        return self.bundle_root.name

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def is_reserved(self) -> bool:
        return self.filename in RESERVED_FILENAMES

    @property
    def is_bundle_root_index(self) -> bool:
        return self.filename == "index.md" and self.path.parent == self.bundle_root

    # -- frontmatter accessors --------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        return self.frontmatter.get(key, default)

    @property
    def type(self) -> str | None:
        value = self.get("type")
        return value.strip() if isinstance(value, str) else value

    @property
    def title(self) -> str:
        value = self.get("title")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return self.path.stem.replace("-", " ").replace("_", " ").title()

    @property
    def description(self) -> str | None:
        return self.get("description")

    @property
    def resource(self) -> str | None:
        return self.get("resource")

    @property
    def owner(self) -> str | None:
        """House field, not spec. See docs/house-profile.md."""
        return self.get("owner")

    @property
    def tags(self) -> list[str]:
        value = self.get("tags") or []
        if isinstance(value, str):
            return [value]
        return [str(tag) for tag in value] if isinstance(value, list) else []

    @property
    def status(self) -> str:
        value = self.get("status")
        return value.strip() if isinstance(value, str) and value.strip() else DEFAULT_STATUS

    @property
    def generated(self) -> dict[str, Any] | None:
        value = self.get("generated")
        return value if isinstance(value, dict) else None

    @property
    def verified(self) -> list[dict[str, Any]]:
        """SPEC §5.2 -- a bare mapping MUST be read as a one-element list."""
        value = self.get("verified")
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return []

    @property
    def sources(self) -> list[dict[str, Any]]:
        value = self.get("sources")
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return []

    # -- derived signals ---------------------------------------------------

    @property
    def trust_tier(self) -> str:
        """SPEC §5.3."""
        events = self.verified
        if not events:
            return TRUST_UNVERIFIED
        for event in events:
            actor = event.get("by")
            if isinstance(actor, str) and actor.strip().startswith("human:"):
                return TRUST_HUMAN
        return TRUST_MACHINE

    @property
    def verified_at(self) -> dt.datetime | None:
        """Latest verification instant, or None."""
        instants = []
        for event in self.verified:
            try:
                instants.append(parse_timestamp(event.get("at")))
            except ValueError:
                continue
        return max(instants) if instants else None

    @property
    def generated_at(self) -> dt.datetime | None:
        source = self.generated or {}
        candidate = source.get("at", self.get("timestamp"))  # §13.1 v0.1 fallback
        try:
            return parse_timestamp(candidate)
        except ValueError:
            return None

    @property
    def stale_after(self) -> dt.datetime | None:
        try:
            return parse_timestamp(self.get("stale_after"))
        except ValueError:
            return None

    def is_stale(self, now: dt.datetime | None = None) -> bool:
        """SPEC §5.5 -- stale when ``now >= stale_after``."""
        deadline = self.stale_after
        if deadline is None:
            return False
        return (now or now_utc()) >= deadline

    # -- body structure ----------------------------------------------------

    @property
    def masked_body(self) -> str:
        return mask_fenced_code(self.body)

    def links(self) -> list[Link]:
        masked = self.masked_body
        return [
            Link(
                text=match.group(1),
                target=match.group(2),
                line=self.body_start_line + line_of(masked, match.start()) - 1,
            )
            for match in _MD_LINK.finditer(masked)
        ]

    def footnote_definitions(self) -> dict[str, int]:
        masked = self.masked_body
        return {
            match.group(1): self.body_start_line + line_of(masked, match.start()) - 1
            for match in _FOOTNOTE_DEF.finditer(masked)
        }

    def footnote_references(self) -> dict[str, int]:
        masked = self.masked_body
        definitions = set(self.footnote_definitions())
        found: dict[str, int] = {}
        for match in _FOOTNOTE_REF.finditer(masked):
            label = match.group(1)
            line_start = masked.rfind("\n", 0, match.start()) + 1
            if label in definitions and masked[line_start : match.start()].strip() == "":
                continue  # this occurrence is the definition itself
            found.setdefault(label, self.body_start_line + line_of(masked, match.start()) - 1)
        return found

    def headings(self) -> list[tuple[int, str, int]]:
        """(level, text, offset-into-body) for every heading outside code fences."""
        masked = self.masked_body
        return [
            (len(match.group(1)), match.group(2).strip(), match.start())
            for match in _HEADING.finditer(masked)
        ]

    def section(self, heading: str) -> str | None:
        """Body text under a heading, case-insensitively matched."""
        wanted = heading.strip().lower()
        found = self.headings()
        for position, (level, text, offset) in enumerate(found):
            if text.lower() != wanted:
                continue
            end = len(self.body)
            for next_level, _, next_offset in found[position + 1 :]:
                if next_level <= level:
                    end = next_offset
                    break
            start = self.body.find("\n", offset)
            return self.body[start + 1 if start != -1 else offset : end].strip()
        return None

    def computation(self) -> str | None:
        """SPEC §10.3 -- the inline computation, fenced or indented.

        Returns None when the concept instead points at a `computation:` file.
        """
        section = self.section("Computation")
        if not section:
            return None
        fenced = re.search(r"(?:```|~~~)[^\n]*\n(.*?)\n(?:```|~~~)", section, re.DOTALL)
        if fenced:
            return fenced.group(1).strip()
        indented = [
            line[4:] for line in section.splitlines() if line.startswith("    ") or not line.strip()
        ]
        text = "\n".join(indented).strip()
        return text or None

    def chunks(self, max_level: int = 2) -> list[Chunk]:
        """Split the body by heading for retrieval (manual §9)."""
        found = [item for item in self.headings() if item[0] <= max_level]
        if not found:
            text = self.body.strip()
            return [Chunk(heading="", level=0, ordinal=0, text=text)] if text else []

        results: list[Chunk] = []
        preamble = self.body[: found[0][2]].strip()
        if preamble:
            results.append(Chunk(heading="", level=0, ordinal=0, text=preamble))

        for position, (level, text, offset) in enumerate(found):
            end = found[position + 1][2] if position + 1 < len(found) else len(self.body)
            content = self.body[offset:end].strip()
            if content:
                results.append(
                    Chunk(heading=text, level=level, ordinal=len(results), text=content)
                )
        return results

    # -- link resolution ---------------------------------------------------

    def resolve(self, target: str) -> Path | None:
        """Resolve a bundle-relative or relative link to a filesystem path.

        Returns None for external URIs and bare anchors (SPEC §6.1, §6.2).
        """
        path_part = target.split("#", 1)[0].strip()
        if not path_part or _EXTERNAL_URI.match(path_part):
            return None
        if path_part.startswith("/"):
            return (self.bundle_root / path_part.lstrip("/")).resolve()
        return (self.path.parent / path_part).resolve()


# --------------------------------------------------------------------------
# bundles
# --------------------------------------------------------------------------


@dataclass
class Bundle:
    root: Path
    concepts: list[Concept] = field(default_factory=list)
    indexes: list[Concept] = field(default_factory=list)
    logs: list[Concept] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def declared_version(self) -> str | None:
        for candidate in self.indexes:
            if candidate.is_bundle_root_index:
                value = candidate.frontmatter.get("okf_version")
                return str(value) if value is not None else None
        return None

    def all_documents(self) -> Iterator[Concept]:
        yield from self.concepts
        yield from self.indexes
        yield from self.logs

    @classmethod
    def load(cls, root: Path) -> "Bundle":
        root = root.resolve()
        bundle = cls(root=root)
        for path in sorted(root.rglob("*.md")):
            if any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            bundle._add(_read_document(root, path))
        return bundle

    def _add(self, concept: Concept) -> None:
        if concept.filename == "index.md":
            self.indexes.append(concept)
        elif concept.filename == "log.md":
            self.logs.append(concept)
        else:
            self.concepts.append(concept)


def _read_document(bundle_root: Path, path: Path) -> Concept:
    raw = path.read_text(encoding="utf-8")
    try:
        frontmatter, body, body_line = split_frontmatter(raw)
        error = None
    except FrontmatterError as exc:
        frontmatter, body, body_line, error = None, raw, 1, str(exc)
    return Concept(
        bundle_root=bundle_root,
        path=path,
        raw=raw,
        frontmatter=frontmatter if frontmatter is not None else {},
        body=body,
        body_start_line=body_line,
        parse_error=error,
    )


def discover_bundles(root: Path) -> list[Bundle]:
    """Load every bundle under ``root``.

    A bundle is a directory directly beneath ``root``. If ``root`` itself
    looks like a bundle (it holds markdown at its top level and no bundle
    subdirectories), it is loaded as a single bundle.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)

    children = [
        child
        for child in sorted(root.iterdir())
        if child.is_dir() and not child.name.startswith((".", "_"))
    ]
    bundle_dirs = [child for child in children if any(child.rglob("*.md"))]
    if bundle_dirs:
        return [Bundle.load(child) for child in bundle_dirs]
    return [Bundle.load(root)] if any(root.glob("*.md")) else []


def iter_concepts(bundles: Iterable[Bundle]) -> Iterator[tuple[Bundle, Concept]]:
    for bundle in bundles:
        for concept in bundle.concepts:
            yield bundle, concept
