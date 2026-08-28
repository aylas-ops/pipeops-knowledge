"""Loader for the house profile.

The OKF specification is deliberately permissive: only `type` is required and
consumers "MUST NOT reject" a bundle for missing optional fields, unknown
types, or broken links (SPEC §11). That permissiveness is right for exchanging
knowledge between organisations and wrong for a repository we maintain -- an
unowned, undated concept is exactly the thing that rots.

The house profile is where we tighten it. Everything here is data, so raising
or relaxing a rule is a reviewable one-line diff rather than a code change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ERROR = "error"
WARNING = "warning"
OFF = "off"
SEVERITIES = (ERROR, WARNING, OFF)

DEFAULT_PROFILE_FILENAME = "okf-profile.yaml"

_DEFAULTS: dict[str, Any] = {
    "profile": "default",
    "okf_version": "0.2",
    "required_fields": [],
    "rules": {},
    "allowed_types": [],
    "unlisted_type": WARNING,
    "owners": [],
    "resource_uri_patterns": [],
    "resource_required_types": [],
    "stale_after_required_types": [],
    "review_cadence_days": {},
    "exempt_paths": [],
}


class ProfileError(ValueError):
    pass


@dataclass
class Profile:
    """House rules layered on top of OKF v0.2 conformance."""

    name: str = "default"
    okf_version: str = "0.2"
    required_fields: list[str] = field(default_factory=list)
    rules: dict[str, str] = field(default_factory=dict)
    allowed_types: list[str] = field(default_factory=list)
    unlisted_type: str = WARNING
    owners: list[str] = field(default_factory=list)
    resource_uri_patterns: list[str] = field(default_factory=list)
    resource_required_types: list[str] = field(default_factory=list)
    stale_after_required_types: list[str] = field(default_factory=list)
    review_cadence_days: dict[str, int] = field(default_factory=dict)
    exempt_paths: list[str] = field(default_factory=list)
    source_path: Path | None = None

    # -- lookups -----------------------------------------------------------

    def severity(self, rule: str, default: str = WARNING) -> str:
        return self.rules.get(rule, default)

    def enabled(self, rule: str, default: str = WARNING) -> bool:
        return self.severity(rule, default) != OFF

    def is_known_owner(self, owner: str | None) -> bool:
        if not self.owners:
            return True  # no registry configured -> any owner accepted
        return isinstance(owner, str) and owner.strip() in set(self.owners)

    def is_known_type(self, type_name: str | None) -> bool:
        if not self.allowed_types:
            return True
        return isinstance(type_name, str) and type_name.strip() in set(self.allowed_types)

    def resource_matches(self, resource: str) -> bool:
        if not self.resource_uri_patterns:
            return True
        return any(re.search(pattern, resource) for pattern in self._compiled_patterns())

    def _compiled_patterns(self) -> list[re.Pattern[str]]:
        if not hasattr(self, "_patterns_cache"):
            object.__setattr__(
                self,
                "_patterns_cache",
                [re.compile(pattern) for pattern in self.resource_uri_patterns],
            )
        return self._patterns_cache  # type: ignore[attr-defined]

    def is_exempt(self, rel_path: str) -> bool:
        return any(Path(rel_path).match(pattern) for pattern in self.exempt_paths)

    def cadence_for(self, type_name: str | None) -> int | None:
        if not isinstance(type_name, str):
            return None
        return self.review_cadence_days.get(type_name)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any], source_path: Path | None = None) -> "Profile":
        merged = {**_DEFAULTS, **(data or {})}

        rules = merged.get("rules") or {}
        if not isinstance(rules, dict):
            raise ProfileError("`rules` must be a mapping of rule name to severity")
        for rule, severity in rules.items():
            if severity not in SEVERITIES:
                raise ProfileError(
                    f"rule {rule!r} has severity {severity!r}; expected one of {SEVERITIES}"
                )

        if merged["unlisted_type"] not in SEVERITIES:
            raise ProfileError(
                f"`unlisted_type` must be one of {SEVERITIES}, got {merged['unlisted_type']!r}"
            )

        cadence = merged.get("review_cadence_days") or {}
        if not isinstance(cadence, dict):
            raise ProfileError("`review_cadence_days` must be a mapping of type to days")

        for pattern in merged.get("resource_uri_patterns") or []:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ProfileError(f"invalid resource_uri_pattern {pattern!r}: {exc}") from exc

        return cls(
            name=str(merged["profile"]),
            okf_version=str(merged["okf_version"]),
            required_fields=list(merged.get("required_fields") or []),
            rules={str(k): str(v) for k, v in rules.items()},
            allowed_types=list(merged.get("allowed_types") or []),
            unlisted_type=str(merged["unlisted_type"]),
            owners=[str(owner) for owner in (merged.get("owners") or [])],
            resource_uri_patterns=[str(p) for p in (merged.get("resource_uri_patterns") or [])],
            resource_required_types=[
                str(t) for t in (merged.get("resource_required_types") or [])
            ],
            stale_after_required_types=[
                str(t) for t in (merged.get("stale_after_required_types") or [])
            ],
            review_cadence_days={str(k): int(v) for k, v in cadence.items()},
            exempt_paths=[str(p) for p in (merged.get("exempt_paths") or [])],
            source_path=source_path,
        )

    @classmethod
    def load(cls, path: Path) -> "Profile":
        path = Path(path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ProfileError(f"{path}: {exc}") from exc
        if data is not None and not isinstance(data, dict):
            raise ProfileError(f"{path}: profile must be a YAML mapping")
        return cls.from_dict(data or {}, source_path=path)

    @classmethod
    def discover(cls, start: Path, explicit: Path | None = None) -> "Profile":
        """Load an explicit profile, or walk up from ``start`` looking for one."""
        if explicit is not None:
            return cls.load(explicit)
        current = Path(start).resolve()
        for candidate_dir in [current, *current.parents]:
            candidate = candidate_dir / DEFAULT_PROFILE_FILENAME
            if candidate.is_file():
                return cls.load(candidate)
        return cls.from_dict({})
