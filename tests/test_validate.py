"""Tests for conformance and house-rule validation.

The point of the broken fixture is regression pressure: every rule the
validator claims to enforce has a document here that violates it, so a rule
that silently stops firing fails a test rather than quietly passing bad
knowledge into the index.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from okf.model import Bundle, parse_timestamp
from okf.profile import ERROR, Profile
from okf.validate import summarize, validate_bundle

REPO = Path(__file__).resolve().parent.parent
REFERENCE = REPO / "bundles" / "reference_example"
BROKEN = Path(__file__).resolve().parent / "fixtures" / "broken_bundle"
NOW = parse_timestamp("2026-08-28T12:00:00Z")


class TestReferenceBundle(unittest.TestCase):
    """The bundle we ship must pass the profile we ship."""

    def test_is_clean_under_the_house_profile(self):
        profile = Profile.load(REPO / "okf-profile.yaml")
        findings = validate_bundle(Bundle.load(REFERENCE), profile, now=NOW)
        detail = "\n".join(finding.format(REPO) for finding in findings)
        self.assertEqual(summarize(findings).get(ERROR, 0), 0, detail)

    def test_has_no_warnings_either(self):
        profile = Profile.load(REPO / "okf-profile.yaml")
        findings = validate_bundle(Bundle.load(REFERENCE), profile, now=NOW)
        detail = "\n".join(finding.format(REPO) for finding in findings)
        self.assertEqual(findings, [], detail)


class TestBrokenBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = Profile.load(REPO / "okf-profile.yaml")
        cls.findings = validate_bundle(Bundle.load(BROKEN), cls.profile, now=NOW)
        cls.rules = {finding.rule for finding in cls.findings}

    def messages(self, rule: str) -> list[str]:
        return [f.message for f in self.findings if f.rule == rule]

    def assert_fires(self, rule: str, needle: str | None = None):
        self.assertIn(rule, self.rules, f"{rule} did not fire. Fired: {sorted(self.rules)}")
        if needle:
            joined = " ".join(self.messages(rule))
            self.assertIn(needle, joined, f"{rule} fired but not for {needle!r}: {joined}")

    # -- conformance -------------------------------------------------------

    def test_missing_frontmatter(self):
        self.assert_fires("frontmatter_parseable", "no YAML frontmatter block")

    def test_unparseable_frontmatter(self):
        self.assert_fires("frontmatter_parseable")
        self.assertTrue(
            any("not parseable YAML" in message for message in self.messages("frontmatter_parseable"))
        )

    def test_missing_type(self):
        self.assert_fires("type_required")

    def test_index_with_a_forbidden_key(self):
        self.assert_fires("index_frontmatter", "okf_version")

    def test_non_iso_log_heading(self):
        self.assert_fires("log_format", "March 3rd 2026")

    def test_retired_v01_status_value(self):
        self.assert_fires("status_enum", "approved")

    def test_naive_timestamp(self):
        self.assert_fires("timestamp_format")

    def test_non_actor_in_generated_by(self):
        self.assert_fires("actor_format", "some agent")

    def test_verified_entry_without_an_actor(self):
        self.assert_fires("verified_shape", "needs a `by` actor")

    def test_source_without_a_resource(self):
        self.assert_fires("sources_shape", "missing `resource`")

    def test_footnote_that_binds_to_nothing(self):
        self.assert_fires("footnote_binding", "nowhere")

    def test_attested_computation_without_a_runtime(self):
        self.assert_fires("computation_contract", "`runtime` is required")

    def test_attested_computation_without_a_computation(self):
        self.assert_fires("computation_contract", "has no computation")

    def test_receipt_that_is_not_a_list(self):
        self.assert_fires("computation_contract", "must be a list of field names")

    def test_missing_attester_code(self):
        self.assert_fires("attester_resource_exists", "attester.resource")

    # -- house rules -------------------------------------------------------

    def test_unregistered_owner(self):
        self.assert_fires("owner_registered", "nobody-in-particular")

    def test_broken_link(self):
        self.assert_fires("broken_links", "./missing.md")

    def test_a_link_inside_a_code_fence_is_not_a_link(self):
        self.assertNotIn("also-missing.md", " ".join(self.messages("broken_links")))

    def test_duplicate_stable_titles(self):
        self.assert_fires("duplicate_titles", "same name")

    def test_unknown_resource_scheme(self):
        self.assert_fires("resource_uri_pattern", "ftp://")

    def test_unlisted_type(self):
        self.assert_fires("unlisted_type", "Widget")

    def test_missing_required_house_fields(self):
        self.assert_fires("required_fields")

    def test_the_whole_fixture_fails_the_build(self):
        self.assertGreater(summarize(self.findings).get(ERROR, 0), 10)


class TestSeverityIsConfigurable(unittest.TestCase):
    def test_a_rule_set_to_off_stops_firing(self):
        profile = Profile.from_dict(
            {"rules": {"broken_links": "off", "required_fields": "off"}, "owners": []}
        )
        findings = validate_bundle(Bundle.load(BROKEN), profile, now=NOW)
        self.assertNotIn("broken_links", {finding.rule for finding in findings})

    def test_a_rule_can_be_downgraded_to_a_warning(self):
        profile = Profile.from_dict({"rules": {"broken_links": "warning"}})
        findings = validate_bundle(Bundle.load(BROKEN), profile, now=NOW)
        broken = [f for f in findings if f.rule == "broken_links"]
        self.assertTrue(broken)
        self.assertTrue(all(finding.severity == "warning" for finding in broken))

    def test_an_empty_owner_registry_accepts_any_actor(self):
        profile = Profile.from_dict({"rules": {"owner_registered": "error"}})
        findings = validate_bundle(Bundle.load(BROKEN), profile, now=NOW)
        # Still flags the non-actor string, but not unknown-but-valid actors.
        messages = " ".join(f.message for f in findings if f.rule == "owner_registered")
        self.assertIn("is not an actor", messages)
        self.assertNotIn("ownership registry", messages)


class TestFreshness(unittest.TestCase):
    def setUp(self):
        self.profile = Profile.load(REPO / "okf-profile.yaml")

    def test_a_bundle_goes_stale_as_time_passes(self):
        later = parse_timestamp("2027-06-01T00:00:00Z")
        findings = validate_bundle(Bundle.load(REFERENCE), self.profile, now=later)
        rules = {finding.rule for finding in findings}
        self.assertIn("stale", rules)
        self.assertIn("review_overdue", rules)
        # Freshness decay is a warning, not a build break.
        self.assertEqual(summarize(findings).get(ERROR, 0), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
