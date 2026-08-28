"""Generation of `index.md` files (SPEC §8).

Index files are what make a bundle navigable by progressive disclosure: an
agent reads the index, decides which concept is worth opening, and never has
to load the whole repository into a prompt. They are pure derived data, so
they are generated rather than hand-maintained -- except for subdirectory
descriptions, which a human writes once and this module preserves.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re

from .model import Bundle, Concept, RESERVED_FILENAMES

_ENTRY = re.compile(r"^\*\s+\[(?P<label>[^\]]+)\]\((?P<target>[^)]+)\)(?:\s+-\s+(?P<desc>.*))?$")


def existing_descriptions(index_path: Path) -> dict[str, str]:
    """Read `target -> description` from an index file already on disk."""
    if not index_path.is_file():
        return {}
    found: dict[str, str] = {}
    for line in index_path.read_text(encoding="utf-8").splitlines():
        match = _ENTRY.match(line.strip())
        if match and match.group("desc"):
            found[match.group("target").strip()] = match.group("desc").strip()
    return found


def prose_sections(index_path: Path) -> list[str]:
    """Return hand-written sections of an index -- those with no link entries.

    Generated listings are safe to overwrite; a note someone wrote by hand is
    not. Keeping prose in its own section makes `okf reindex` non-destructive.
    """
    if not index_path.is_file():
        return []
    text = index_path.read_text(encoding="utf-8")
    if text.startswith("---"):  # skip a root okf_version block
        closing = text.find("\n---", 3)
        if closing != -1:
            text = text[text.find("\n", closing + 1) + 1 :]

    kept: list[str] = []
    current: list[str] = []
    has_entries = False
    for line in text.splitlines():
        if line.startswith("# "):
            if current and not has_entries:
                kept.append("\n".join(current).strip())
            current, has_entries = [line], False
        else:
            if _ENTRY.match(line.strip()):
                has_entries = True
            current.append(line)
    if current and not has_entries and any(part.strip() for part in current):
        kept.append("\n".join(current).strip())
    return [section for section in kept if section]


def render_index(
    directory: Path,
    bundle: Bundle,
    *,
    okf_version: str | None = None,
    preserved: dict[str, str] | None = None,
    prose: list[str] | None = None,
) -> str:
    """Render the index for one directory of a bundle."""
    preserved = preserved or {}
    lines: list[str] = []

    if okf_version:
        lines += ["---", f'okf_version: "{okf_version}"', "---", ""]

    subdirectories = sorted(
        child
        for child in directory.iterdir()
        # Includes directories holding only non-markdown members, such as
        # `references/attesters/` -- they are still part of the bundle.
        if child.is_dir() and not child.name.startswith(".") and any(child.iterdir())
    )
    if subdirectories:
        lines.append("# Subdirectories")
        lines.append("")
        for child in subdirectories:
            target = f"{child.name}/"
            description = preserved.get(target) or preserved.get(f"{child.name}/index.md")
            suffix = f" - {description}" if description else ""
            lines.append(f"* [{child.name}]({target}){suffix}")
        lines.append("")

    here = [
        concept
        for concept in bundle.concepts
        if concept.path.parent == directory and concept.filename not in RESERVED_FILENAMES
    ]
    grouped: dict[str, list[Concept]] = defaultdict(list)
    for concept in here:
        grouped[concept.type or "Concept"].append(concept)

    for type_name in sorted(grouped):
        lines.append(f"# {type_name}")
        lines.append("")
        for concept in sorted(grouped[type_name], key=lambda item: item.title.lower()):
            target = concept.path.name
            description = concept.description or preserved.get(target) or ""
            marker = ""
            if concept.status == "draft":
                marker = " *(draft — awaiting owner review)*"
            elif concept.status == "deprecated":
                marker = " *(deprecated)*"
            suffix = f" - {description}{marker}" if description else marker
            lines.append(f"* [{concept.title}]({target}){suffix}")
        lines.append("")

    if not subdirectories and not here:
        lines += ["# Contents", "", "*(empty)*", ""]

    for section in prose or []:
        lines += [section, ""]

    return "\n".join(lines).rstrip() + "\n"


def write_indexes(
    bundle: Bundle, *, okf_version: str = "0.2", dry_run: bool = False
) -> list[tuple[Path, bool]]:
    """Regenerate every index.md in a bundle.

    Returns `(path, changed)` pairs so callers can report without diffing.
    """
    directories = {concept.path.parent for concept in bundle.concepts} | {bundle.root}
    for index in bundle.indexes:
        directories.add(index.path.parent)

    results: list[tuple[Path, bool]] = []
    for directory in sorted(directories):
        index_path = directory / "index.md"
        content = render_index(
            directory,
            bundle,
            okf_version=okf_version if directory == bundle.root else None,
            preserved=existing_descriptions(index_path),
            prose=prose_sections(index_path),
        )
        previous = index_path.read_text(encoding="utf-8") if index_path.is_file() else None
        changed = previous != content
        if changed and not dry_run:
            index_path.write_text(content, encoding="utf-8")
        results.append((index_path, changed))
    return results
