"""Validation for OKF v0.2 bundles.

Two layers, deliberately kept apart:

* **Conformance** -- the rules SPEC §11 actually requires. A bundle that
  fails these is not OKF and no consumer should be expected to read it.
* **House rules** -- everything `okf-profile.yaml` adds on top. The spec is
  permissive by design; a repository we maintain cannot afford to be.

Every check yields :class:`Finding` objects rather than printing, so the same
checks drive the CLI, CI annotations, and the tests.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .model import (
    Bundle,
    Concept,
    STATUS_VALUES,
    format_timestamp,
    is_actor,
    line_of,
    now_utc,
    parse_timestamp,
)
from .profile import ERROR, OFF, WARNING, Profile

# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    severity: str
    rule: str
    message: str
    path: str
    line: int | None = None

    def format(self, root: Path | None = None) -> str:
        location = self.path
        if root is not None:
            try:
                location = str(Path(self.path).relative_to(root))
            except ValueError:
                pass
        if self.line:
            location = f"{location}:{self.line}"
        return f"{location}: {self.severity}: [{self.rule}] {self.message}"

    def as_github_annotation(self, root: Path | None = None) -> str:
        level = "error" if self.severity == ERROR else "warning"
        location = self.path
        if root is not None:
            try:
                location = str(Path(self.path).relative_to(root))
            except ValueError:
                pass
        line = f",line={self.line}" if self.line else ""
        message = self.message.replace("\n", " ").replace("%", "%25")
        return f"::{level} file={location}{line}::[{self.rule}] {message}"


class _Reporter:
    """Collects findings, applying the profile's severity for each rule."""

    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self.findings: list[Finding] = []

    def add(
        self,
        rule: str,
        concept: Concept | None,
        message: str,
        *,
        line: int | None = None,
        path: Path | None = None,
        default: str = WARNING,
    ) -> None:
        severity = self.profile.severity(rule, default)
        if severity == OFF:
            return
        target = path if path is not None else (concept.path if concept else Path("."))
        self.findings.append(
            Finding(
                severity=severity,
                rule=rule,
                message=message,
                path=str(target),
                line=line,
            )
        )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _frontmatter_line(concept: Concept, key: str) -> int:
    """Best-effort line number of a top-level frontmatter key."""
    match = re.search(rf"^{re.escape(key)}\s*:", concept.raw, re.MULTILINE)
    return line_of(concept.raw, match.start()) if match else 1


def _check_timestamp(
    reporter: _Reporter, concept: Concept, label: str, value: Any, key_for_line: str
) -> dt.datetime | None:
    if value is None:
        return None
    try:
        return parse_timestamp(value)
    except ValueError as exc:
        reporter.add(
            "timestamp_format",
            concept,
            f"{label}: {exc}. SPEC §5 requires ISO 8601 with an explicit UTC "
            f"offset, e.g. {format_timestamp(now_utc())}",
            line=_frontmatter_line(concept, key_for_line),
            default=ERROR,
        )
        return None


def _check_actor(
    reporter: _Reporter, concept: Concept, label: str, value: Any, key_for_line: str
) -> None:
    if value is None:
        return
    if not is_actor(value):
        reporter.add(
            "actor_format",
            concept,
            f"{label}: {value!r} is not an actor. SPEC §7 expects "
            "`human:<id>`, `process:<id>`, or `<producer>/<version>`",
            line=_frontmatter_line(concept, key_for_line),
            default=ERROR,
        )


# --------------------------------------------------------------------------
# conformance checks (SPEC §11)
# --------------------------------------------------------------------------


def check_frontmatter(reporter: _Reporter, bundle: Bundle) -> None:
    """§4.1, §11.1-2: parseable frontmatter with a non-empty `type`."""
    for concept in bundle.concepts:
        if concept.parse_error:
            reporter.add(
                "frontmatter_parseable",
                concept,
                f"frontmatter is not parseable YAML: {concept.parse_error}",
                line=1,
                default=ERROR,
            )
            continue
        if not concept.raw.lstrip().startswith("---"):
            reporter.add(
                "frontmatter_parseable",
                concept,
                "no YAML frontmatter block. Every concept document needs one "
                "(SPEC §4.1); only index.md and log.md are exempt",
                line=1,
                default=ERROR,
            )
            continue
        if not concept.type:
            reporter.add(
                "type_required",
                concept,
                "missing `type`, the one always-required key (SPEC §4.1)",
                line=1,
                default=ERROR,
            )


def check_index_files(reporter: _Reporter, bundle: Bundle) -> None:
    """§8: index.md carries no frontmatter, except `okf_version` at the root."""
    for index in bundle.indexes:
        if index.parse_error:
            reporter.add(
                "index_frontmatter",
                index,
                f"index.md frontmatter is not parseable: {index.parse_error}",
                line=1,
                default=ERROR,
            )
            continue
        if not index.frontmatter:
            continue
        extra = set(index.frontmatter) - {"okf_version"}
        if not index.is_bundle_root_index:
            reporter.add(
                "index_frontmatter",
                index,
                "index.md must not carry frontmatter (SPEC §8). Only a "
                "bundle-root index.md may, and only `okf_version`",
                line=1,
                default=ERROR,
            )
        elif extra:
            reporter.add(
                "index_frontmatter",
                index,
                f"bundle-root index.md may only carry `okf_version`; found "
                f"{', '.join(sorted(extra))} (SPEC §8, §12)",
                line=1,
                default=ERROR,
            )


_LOG_DATE_HEADING = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def check_log_files(reporter: _Reporter, bundle: Bundle) -> None:
    """§9: date headings use ISO 8601 `YYYY-MM-DD`."""
    for log in bundle.logs:
        for match in _LOG_DATE_HEADING.finditer(log.masked_body):
            heading = match.group(1).strip()
            try:
                dt.date.fromisoformat(heading)
            except ValueError:
                reporter.add(
                    "log_format",
                    log,
                    f"log heading {heading!r} is not an ISO 8601 date. "
                    "SPEC §9 requires `## YYYY-MM-DD`",
                    line=log.body_start_line + line_of(log.masked_body, match.start()) - 1,
                    default=ERROR,
                )


def check_trust_families(reporter: _Reporter, bundle: Bundle) -> None:
    """§5.2, §5.4, §7: shapes of `generated`, `verified`, `status`."""
    for concept in bundle.concepts:
        generated = concept.get("generated")
        if generated is not None:
            if not isinstance(generated, dict):
                reporter.add(
                    "verified_shape",
                    concept,
                    "`generated` must be a mapping of `{ by, at }` (SPEC §5.2)",
                    line=_frontmatter_line(concept, "generated"),
                    default=ERROR,
                )
            else:
                if "by" not in generated:
                    reporter.add(
                        "verified_shape",
                        concept,
                        "`generated.by` is required within `generated` (SPEC §5.2)",
                        line=_frontmatter_line(concept, "generated"),
                        default=ERROR,
                    )
                _check_actor(reporter, concept, "generated.by", generated.get("by"), "generated")
                _check_timestamp(
                    reporter, concept, "generated.at", generated.get("at"), "generated"
                )

        raw_verified = concept.get("verified")
        if raw_verified is not None and not isinstance(raw_verified, (dict, list)):
            reporter.add(
                "verified_shape",
                concept,
                "`verified` must be a `{ by, at }` mapping or a list of them (SPEC §5.2)",
                line=_frontmatter_line(concept, "verified"),
                default=ERROR,
            )
        for event in concept.verified:
            if "by" not in event:
                reporter.add(
                    "verified_shape",
                    concept,
                    "each `verified` entry needs a `by` actor (SPEC §5.2)",
                    line=_frontmatter_line(concept, "verified"),
                    default=ERROR,
                )
            _check_actor(reporter, concept, "verified[].by", event.get("by"), "verified")
            _check_timestamp(reporter, concept, "verified[].at", event.get("at"), "verified")

        _check_timestamp(reporter, concept, "stale_after", concept.get("stale_after"), "stale_after")

        status = concept.get("status")
        if status is not None and status not in STATUS_VALUES:
            reporter.add(
                "status_enum",
                concept,
                f"`status: {status}` is not valid. SPEC §5.4 allows "
                f"{' | '.join(sorted(STATUS_VALUES))}. (The v0.1-era values "
                "`reviewed`/`approved`/`archived` were retired: use `stable` "
                "plus a `verified` entry, or `deprecated`.)",
                line=_frontmatter_line(concept, "status"),
                default=ERROR,
            )


def check_sources(reporter: _Reporter, bundle: Bundle) -> None:
    """§5.1: `sources` entries and their credibility signals."""
    for concept in bundle.concepts:
        raw = concept.get("sources")
        if raw is None:
            continue
        if not isinstance(raw, list):
            reporter.add(
                "sources_shape",
                concept,
                "`sources` must be a list of entries (SPEC §5.1)",
                line=_frontmatter_line(concept, "sources"),
                default=ERROR,
            )
            continue

        line = _frontmatter_line(concept, "sources")
        seen_ids: set[str] = set()
        for position, entry in enumerate(raw):
            if not isinstance(entry, dict):
                reporter.add(
                    "sources_shape",
                    concept,
                    f"sources[{position}] must be a mapping (SPEC §5.1)",
                    line=line,
                    default=ERROR,
                )
                continue
            if not entry.get("resource"):
                reporter.add(
                    "sources_shape",
                    concept,
                    f"sources[{position}] is missing `resource`, which is "
                    "required within an entry (SPEC §5.1)",
                    line=line,
                    default=ERROR,
                )
            source_id = entry.get("id")
            if source_id is not None:
                if source_id in seen_ids:
                    reporter.add(
                        "sources_shape",
                        concept,
                        f"duplicate sources[].id {source_id!r}; ids are the join "
                        "key for footnote attribution and must be unique (SPEC §5.1)",
                        line=line,
                        default=ERROR,
                    )
                seen_ids.add(source_id)
            _check_actor(reporter, concept, f"sources[{position}].author", entry.get("author"), "sources")
            _check_timestamp(
                reporter,
                concept,
                f"sources[{position}].last_modified",
                entry.get("last_modified"),
                "sources",
            )
            usage_count = entry.get("usage_count")
            if usage_count is not None and not isinstance(usage_count, int):
                reporter.add(
                    "sources_shape",
                    concept,
                    f"sources[{position}].usage_count must be an integer (SPEC §5.1)",
                    line=line,
                    default=ERROR,
                )

        window = concept.get("usage_window")
        if window is not None:
            if not isinstance(window, dict) or not {"from", "to"} <= set(window):
                reporter.add(
                    "sources_shape",
                    concept,
                    "`usage_window` must be `{ from, to }` (SPEC §5.1)",
                    line=_frontmatter_line(concept, "usage_window"),
                    default=ERROR,
                )
            else:
                _check_timestamp(reporter, concept, "usage_window.from", window["from"], "usage_window")
                _check_timestamp(reporter, concept, "usage_window.to", window["to"], "usage_window")

        has_counts = any(
            isinstance(entry, dict) and entry.get("usage_count") is not None for entry in raw
        )
        entry_windows = all(
            isinstance(entry, dict) and entry.get("usage_window") is not None
            for entry in raw
            if isinstance(entry, dict) and entry.get("usage_count") is not None
        )
        if has_counts and window is None and not entry_windows:
            reporter.add(
                "sources_shape",
                concept,
                "`usage_count` is set but no `usage_window` frames it; the "
                "count is uninterpretable without a range (SPEC §5.1)",
                line=_frontmatter_line(concept, "sources"),
                default=WARNING,
            )


def check_footnotes(reporter: _Reporter, bundle: Bundle) -> None:
    """§5.1: a `[^label]` footnote joins to a `sources[].id`."""
    for concept in bundle.concepts:
        references = concept.footnote_references()
        if not references:
            continue
        known = {
            entry.get("id")
            for entry in concept.sources
            if isinstance(entry.get("id"), str)
        }
        for label, line in references.items():
            if label not in known:
                reporter.add(
                    "footnote_binding",
                    concept,
                    f"footnote [^{label}] has no matching `sources[].id`. The "
                    "label is the join key for per-claim attribution (SPEC §5.1)",
                    line=line,
                    default=ERROR,
                )
        for label, line in concept.footnote_definitions().items():
            if label not in references and label not in known:
                reporter.add(
                    "footnote_binding",
                    concept,
                    f"footnote definition [^{label}] is never referenced and "
                    "matches no `sources[].id`",
                    line=line,
                    default=WARNING,
                )


def check_computations(reporter: _Reporter, bundle: Bundle) -> None:
    """§10: the Attested Computation contract."""
    for concept in bundle.concepts:
        if concept.type != "Attested Computation":
            continue

        if not concept.get("runtime"):
            reporter.add(
                "computation_contract",
                concept,
                "`runtime` is required for `type: Attested Computation`; it is "
                "what tells the executor and attester how to read the "
                "computation and what `parameters` mean (SPEC §10.2)",
                line=1,
                default=ERROR,
            )

        computation_path = concept.get("computation")
        inline = concept.computation()
        if computation_path and inline:
            reporter.add(
                "computation_contract",
                concept,
                "declares both a `computation:` file and an inline "
                "`# Computation` fence; provide exactly one (SPEC §10.3)",
                line=_frontmatter_line(concept, "computation"),
                default=ERROR,
            )
        elif not computation_path and not inline:
            reporter.add(
                "computation_contract",
                concept,
                "has no computation: add a fenced code block under a "
                "`# Computation` heading, or set `computation:` to a file (SPEC §10.3)",
                line=1,
                default=ERROR,
            )
        elif computation_path:
            resolved = concept.resolve(str(computation_path))
            if resolved is not None and not resolved.exists():
                reporter.add(
                    "computation_contract",
                    concept,
                    f"`computation: {computation_path}` does not resolve to a file",
                    line=_frontmatter_line(concept, "computation"),
                    default=ERROR,
                )

        parameters = concept.get("parameters")
        if parameters is not None:
            if not isinstance(parameters, list):
                reporter.add(
                    "computation_contract",
                    concept,
                    "`parameters` must be a list of `{ name, type, required }` (SPEC §10.2)",
                    line=_frontmatter_line(concept, "parameters"),
                    default=ERROR,
                )
            else:
                for position, parameter in enumerate(parameters):
                    if not isinstance(parameter, dict) or "name" not in parameter:
                        reporter.add(
                            "computation_contract",
                            concept,
                            f"parameters[{position}] must be a mapping with at "
                            "least `name` (SPEC §10.2)",
                            line=_frontmatter_line(concept, "parameters"),
                            default=ERROR,
                        )
                        continue
                    for key in ("type", "required"):
                        if key not in parameter:
                            reporter.add(
                                "computation_contract",
                                concept,
                                f"parameters[{position}] ({parameter['name']}) is "
                                f"missing `{key}`; a typed surface is what makes "
                                "attestation mechanical (SPEC §10.3)",
                                line=_frontmatter_line(concept, "parameters"),
                                default=WARNING,
                            )

        executor = concept.get("executor")
        if isinstance(executor, dict):
            receipt = executor.get("receipt")
            if receipt is not None and not isinstance(receipt, list):
                reporter.add(
                    "computation_contract",
                    concept,
                    "`executor.receipt` must be a list of field names the run "
                    "returns (SPEC §10.2)",
                    line=_frontmatter_line(concept, "executor"),
                    default=ERROR,
                )
            _check_path_field(reporter, concept, "executor.resource", executor.get("resource"), "executor")

        attester = concept.get("attester")
        if isinstance(attester, dict):
            _check_path_field(
                reporter,
                concept,
                "attester.resource",
                attester.get("resource"),
                "attester",
                rule="attester_resource_exists",
            )


def _check_path_field(
    reporter: _Reporter,
    concept: Concept,
    label: str,
    value: Any,
    key_for_line: str,
    rule: str = "computation_contract",
) -> None:
    if not isinstance(value, str) or not value.strip():
        return
    resolved = concept.resolve(value)
    if resolved is None:
        return  # external URI -- nothing to check on disk
    if not resolved.exists():
        hint = ""
        if not value.startswith(("/", ".")):
            hint = (
                f". Paths are resolved relative to this file; write "
                f"`/{value.lstrip('/')}` for the bundle-relative form (SPEC §6.2)"
            )
        reporter.add(
            rule,
            concept,
            f"`{label}: {value}` does not resolve to a file in the bundle{hint}",
            line=_frontmatter_line(concept, key_for_line),
            default=ERROR,
        )


def check_declared_version(reporter: _Reporter, bundle: Bundle, profile: Profile) -> None:
    """§12: the bundle-root index declares the version it targets."""
    declared = bundle.declared_version
    if declared is None:
        reporter.add(
            "missing_index",
            None,
            f"bundle {bundle.name!r} does not declare `okf_version` in its "
            "root index.md (SPEC §12)",
            path=bundle.root / "index.md",
            default=WARNING,
        )
    elif declared != profile.okf_version:
        reporter.add(
            "missing_index",
            None,
            f"bundle {bundle.name!r} declares okf_version {declared!r} but the "
            f"house profile targets {profile.okf_version!r}",
            path=bundle.root / "index.md",
            default=WARNING,
        )


# --------------------------------------------------------------------------
# house rules
# --------------------------------------------------------------------------


def check_required_fields(reporter: _Reporter, bundle: Bundle, profile: Profile) -> None:
    for concept in bundle.concepts:
        if profile.is_exempt(concept.rel_path) or concept.parse_error:
            continue
        for key in profile.required_fields:
            value = concept.get(key)
            missing = value is None or (isinstance(value, (str, list, dict)) and len(value) == 0)
            if missing:
                reporter.add(
                    "required_fields",
                    concept,
                    f"missing required field `{key}` (house profile "
                    f"{profile.name!r}). The spec makes it optional; we do not",
                    line=1,
                    default=ERROR,
                )


def check_owners(reporter: _Reporter, bundle: Bundle, profile: Profile) -> None:
    for concept in bundle.concepts:
        if profile.is_exempt(concept.rel_path):
            continue
        owner = concept.owner
        if owner is None:
            continue  # absence is `required_fields`' business, not this rule's
        if not is_actor(owner):
            reporter.add(
                "owner_registered",
                concept,
                f"`owner: {owner}` is not an actor; use `team:<id>` or "
                "`human:<id>` (SPEC §7 convention)",
                line=_frontmatter_line(concept, "owner"),
                default=ERROR,
            )
        elif not profile.is_known_owner(owner):
            reporter.add(
                "owner_registered",
                concept,
                f"`owner: {owner}` is not in the ownership registry. Add them "
                f"to `owners:` in {profile.source_path.name if profile.source_path else 'okf-profile.yaml'} "
                "in the same PR",
                line=_frontmatter_line(concept, "owner"),
                default=ERROR,
            )

        for entry in concept.sources:
            author = entry.get("author")
            if author and is_actor(author) and not profile.is_known_owner(author):
                reporter.add(
                    "owner_registered",
                    concept,
                    f"`sources[].author: {author}` is not in the ownership registry",
                    line=_frontmatter_line(concept, "sources"),
                    default=WARNING,
                )


def check_types(reporter: _Reporter, bundle: Bundle, profile: Profile) -> None:
    for concept in bundle.concepts:
        if not concept.type or profile.is_exempt(concept.rel_path):
            continue
        if not profile.is_known_type(concept.type):
            reporter.add(
                "unlisted_type",
                concept,
                f"`type: {concept.type}` is not in `allowed_types`. The spec "
                "forbids a central registry, so new types are fine -- add it to "
                "the profile in the PR that introduces it",
                line=_frontmatter_line(concept, "type"),
                default=profile.unlisted_type,
            )


def check_links(reporter: _Reporter, bundle: Bundle, profile: Profile) -> None:
    """SPEC §6.1 says consumers MUST tolerate broken links. We refuse to ship them."""
    for concept in bundle.all_documents():
        for link in concept.links():
            if link.is_external or link.is_anchor or not link.path_part:
                continue
            resolved = concept.resolve(link.target)
            if resolved is None:
                continue
            if resolved.exists():
                continue
            # A trailing-slash link to a directory is the index-file shorthand
            # used by index.md entries (SPEC §8).
            if link.path_part.endswith("/") and (resolved / "index.md").exists():
                continue
            reporter.add(
                "broken_links",
                concept,
                f"link target {link.target!r} does not exist in the bundle. "
                "(The spec tolerates this as not-yet-written knowledge; the "
                "house profile does not.)",
                line=link.line,
                default=ERROR,
            )


def check_duplicates(reporter: _Reporter, bundle: Bundle, profile: Profile) -> None:
    """Manual §8: no duplicate approved names within a type."""
    groups: dict[tuple[str, str], list[Concept]] = defaultdict(list)
    for concept in bundle.concepts:
        if concept.status != "stable" or not concept.type:
            continue
        groups[(concept.type, concept.title.strip().lower())].append(concept)

    for (type_name, title), members in sorted(groups.items()):
        if len(members) < 2:
            continue
        others = ", ".join(sorted(member.concept_id for member in members))
        for concept in members:
            reporter.add(
                "duplicate_titles",
                concept,
                f"two or more stable `{type_name}` concepts share the title "
                f"{title!r} ({others}). Deprecate one, or make the titles "
                "distinguish them",
                line=_frontmatter_line(concept, "title"),
                default=ERROR,
            )


def check_resources(reporter: _Reporter, bundle: Bundle, profile: Profile) -> None:
    required = set(profile.resource_required_types)
    for concept in bundle.concepts:
        if profile.is_exempt(concept.rel_path):
            continue
        resource = concept.resource
        if resource is None:
            if concept.type in required:
                reporter.add(
                    "resource_required",
                    concept,
                    f"`type: {concept.type}` describes a concrete asset and must "
                    "name its `resource` so a reader can reach the source of truth",
                    line=1,
                    default=ERROR,
                )
            continue
        if not isinstance(resource, str) or not profile.resource_matches(resource):
            reporter.add(
                "resource_uri_pattern",
                concept,
                f"`resource: {resource}` matches no known URI pattern. Add the "
                "scheme to `resource_uri_patterns` if it is legitimate",
                line=_frontmatter_line(concept, "resource"),
                default=ERROR,
            )


def check_freshness(
    reporter: _Reporter, bundle: Bundle, profile: Profile, now: dt.datetime
) -> None:
    required = set(profile.stale_after_required_types)
    for concept in bundle.concepts:
        if profile.is_exempt(concept.rel_path) or concept.status == "deprecated":
            continue

        if concept.type in required and concept.stale_after is None:
            reporter.add(
                "stale_after_required",
                concept,
                f"`type: {concept.type}` has a review cycle and must declare "
                "`stale_after` (SPEC §5.5), so a consumer can refuse to serve "
                "it once it expires",
                line=1,
                default=ERROR,
            )

        if concept.is_stale(now):
            reporter.add(
                "stale",
                concept,
                f"stale: `stale_after` was {format_timestamp(concept.stale_after)} "
                "and has passed. Re-verify and push the date, or deprecate it",
                line=_frontmatter_line(concept, "stale_after"),
                default=WARNING,
            )

        if concept.status == "draft":
            reporter.add(
                "draft_on_main",
                concept,
                "still `status: draft` -- generated content awaiting owner "
                "review. Retrieval excludes drafts by default",
                line=_frontmatter_line(concept, "status"),
                default=WARNING,
            )

        if concept.status == "stable" and concept.trust_tier == "unverified":
            reporter.add(
                "unverified",
                concept,
                "`status: stable` but no `verified` entry: nothing has confirmed "
                "it against its sources. Trust tier is `unverified` (SPEC §5.3)",
                line=1,
                default=WARNING,
            )

        cadence = profile.cadence_for(concept.type)
        verified_at = concept.verified_at
        if cadence and verified_at is not None:
            age = (now - verified_at).days
            if age > cadence:
                reporter.add(
                    "review_overdue",
                    concept,
                    f"last verified {age} days ago; the review cadence for "
                    f"`{concept.type}` is {cadence} days",
                    line=_frontmatter_line(concept, "verified"),
                    default=WARNING,
                )


def check_descriptions(reporter: _Reporter, bundle: Bundle, profile: Profile) -> None:
    for concept in bundle.concepts:
        description = concept.description
        if not isinstance(description, str):
            continue
        if len(description) > 240:
            reporter.add(
                "description_length",
                concept,
                f"`description` is {len(description)} characters. It is a "
                "one-sentence summary used for previews and retrieval ranking "
                "(SPEC §4.1) -- move the detail into the body",
                line=_frontmatter_line(concept, "description"),
                default=WARNING,
            )


def check_index_coverage(reporter: _Reporter, bundle: Bundle, profile: Profile) -> None:
    """§8: progressive disclosure only works if the index files exist."""
    directories = {concept.path.parent for concept in bundle.concepts} | {bundle.root}
    present = {index.path.parent for index in bundle.indexes}
    for directory in sorted(directories - present):
        reporter.add(
            "missing_index",
            None,
            f"{directory.relative_to(bundle.root).as_posix() or '.'}/ has no "
            "index.md; agents lose progressive disclosure over it (SPEC §8). "
            "Run `okf reindex` to generate one",
            path=directory / "index.md",
            default=WARNING,
        )


def check_orphans(reporter: _Reporter, bundle: Bundle, profile: Profile) -> None:
    """A concept nothing links to and no index lists is unreachable in practice."""
    linked: set[Path] = set()
    for document in bundle.all_documents():
        for link in document.links():
            resolved = document.resolve(link.target)
            if resolved is not None:
                linked.add(resolved)

    for concept in bundle.concepts:
        if profile.is_exempt(concept.rel_path) or concept.status == "deprecated":
            continue
        if concept.path.resolve() not in linked:
            reporter.add(
                "orphan",
                concept,
                "nothing links to this concept and no index.md lists it; it is "
                "reachable only by full-text search",
                line=1,
                default=WARNING,
            )


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def validate_bundle(
    bundle: Bundle, profile: Profile, now: dt.datetime | None = None
) -> list[Finding]:
    now = now or now_utc()
    reporter = _Reporter(profile)

    # Conformance (SPEC §11).
    check_frontmatter(reporter, bundle)
    check_index_files(reporter, bundle)
    check_log_files(reporter, bundle)
    check_trust_families(reporter, bundle)
    check_sources(reporter, bundle)
    check_footnotes(reporter, bundle)
    check_computations(reporter, bundle)
    check_declared_version(reporter, bundle, profile)

    # House rules.
    check_required_fields(reporter, bundle, profile)
    check_owners(reporter, bundle, profile)
    check_types(reporter, bundle, profile)
    check_links(reporter, bundle, profile)
    check_duplicates(reporter, bundle, profile)
    check_resources(reporter, bundle, profile)
    check_freshness(reporter, bundle, profile, now)
    check_descriptions(reporter, bundle, profile)
    check_index_coverage(reporter, bundle, profile)
    check_orphans(reporter, bundle, profile)

    return sorted(reporter.findings, key=lambda f: (f.path, f.line or 0, f.rule))


def validate_bundles(
    bundles: Iterable[Bundle], profile: Profile, now: dt.datetime | None = None
) -> list[Finding]:
    findings: list[Finding] = []
    for bundle in bundles:
        findings.extend(validate_bundle(bundle, profile, now=now))
    return findings


def summarize(findings: Iterable[Finding]) -> dict[str, int]:
    counts = {ERROR: 0, WARNING: 0}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return counts


def render(
    findings: list[Finding],
    fmt: str = "text",
    root: Path | None = None,
) -> str:
    if fmt == "json":
        return json.dumps([asdict(finding) for finding in findings], indent=2)
    if fmt == "github":
        return "\n".join(finding.as_github_annotation(root) for finding in findings)
    return "\n".join(finding.format(root) for finding in findings)
