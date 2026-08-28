"""Tests for the parser and object model."""

from __future__ import annotations

import datetime as dt
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from okf.model import (
    Bundle,
    FrontmatterError,
    format_timestamp,
    is_actor,
    mask_fenced_code,
    parse_timestamp,
    render_frontmatter,
    split_frontmatter,
)

REPO = Path(__file__).resolve().parent.parent
REFERENCE = REPO / "bundles" / "reference_example"


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestFrontmatter(unittest.TestCase):
    def test_splits_frontmatter_and_body(self):
        frontmatter, body, line = split_frontmatter("---\ntype: Metric\n---\n\n# Body\n")
        self.assertEqual(frontmatter, {"type": "Metric"})
        self.assertEqual(body.strip(), "# Body")
        self.assertEqual(line, 4)

    def test_absent_frontmatter_returns_none(self):
        frontmatter, body, line = split_frontmatter("# Just a body\n")
        self.assertIsNone(frontmatter)
        self.assertEqual(line, 1)

    def test_invalid_yaml_raises(self):
        with self.assertRaises(FrontmatterError):
            split_frontmatter("---\ntags: [unclosed\n---\n")

    def test_non_mapping_raises(self):
        with self.assertRaises(FrontmatterError):
            split_frontmatter("---\n- a\n- b\n---\n")

    def test_a_horizontal_rule_in_the_body_is_not_a_delimiter(self):
        frontmatter, body, _ = split_frontmatter("---\ntype: Metric\n---\n\ntext\n\n---\n\nmore\n")
        self.assertEqual(frontmatter, {"type": "Metric"})
        self.assertIn("more", body)


class TestRenderFrontmatter(unittest.TestCase):
    def test_round_trips(self):
        data = {
            "type": "Metric",
            "title": "Revenue",
            "tags": ["finance", "revenue"],
            "generated": {"by": "human:ada", "at": "2026-06-20T22:53:05Z"},
            "sources": [{"id": "policy", "resource": "https://example.org", "title": "Policy"}],
        }
        parsed, _, _ = split_frontmatter(render_frontmatter(data) + "\nbody\n")
        self.assertEqual(parsed["type"], "Metric")
        self.assertEqual(parsed["tags"], ["finance", "revenue"])
        self.assertEqual(parsed["generated"]["by"], "human:ada")
        self.assertEqual(parsed["sources"][0]["id"], "policy")

    def test_quotes_values_that_would_otherwise_break_yaml(self):
        rendered = render_frontmatter({"type": "Skill", "description": "Runtime: postgres"})
        parsed, _, _ = split_frontmatter(rendered + "\n")
        self.assertEqual(parsed["description"], "Runtime: postgres")

    def test_drops_empty_values(self):
        self.assertNotIn("tags", render_frontmatter({"type": "X", "tags": []}))

    def test_urls_and_actors_stay_unquoted(self):
        rendered = render_frontmatter(
            {
                "type": "Service",
                "resource": "https://github.com/example-org/thing",
                "owner": "team:platform",
                "generated": {"by": "process:extract_github", "at": "2026-08-28T11:10:37Z"},
            }
        )
        self.assertIn("resource: https://github.com/example-org/thing", rendered)
        self.assertIn("owner: team:platform", rendered)
        self.assertIn("{ by: process:extract_github,", rendered)

        parsed, _, _ = split_frontmatter(rendered + "\n")
        self.assertEqual(parsed["resource"], "https://github.com/example-org/thing")
        self.assertEqual(parsed["generated"]["by"], "process:extract_github")
        self.assertEqual(
            format_timestamp(parse_timestamp(parsed["generated"]["at"])),
            "2026-08-28T11:10:37Z",
        )

    def test_strings_that_would_change_meaning_are_quoted(self):
        for value in ("Runtime: postgres", "true", "42", "- leading dash", " padded "):
            rendered = render_frontmatter({"type": "X", "description": value})
            parsed, _, _ = split_frontmatter(rendered + "\n")
            self.assertEqual(parsed["description"], value, rendered)


class TestTimestamps(unittest.TestCase):
    def test_accepts_zulu(self):
        self.assertEqual(parse_timestamp("2026-06-20T22:53:05Z").tzinfo, dt.timezone.utc)

    def test_rejects_naive_datetime(self):
        with self.assertRaises(ValueError):
            parse_timestamp("2026-06-20T22:53:05")

    def test_rejects_bare_date(self):
        with self.assertRaises(ValueError):
            parse_timestamp(dt.date(2026, 6, 20))

    def test_formats_utc_with_z(self):
        moment = dt.datetime(2026, 6, 20, 22, 53, 5, tzinfo=dt.timezone.utc)
        self.assertEqual(format_timestamp(moment), "2026-06-20T22:53:05Z")


class TestActors(unittest.TestCase):
    def test_accepts_spec_forms(self):
        for actor in ("human:ada", "process:nightly", "team:platform", "agent/gemini-2.5-pro"):
            self.assertTrue(is_actor(actor), actor)

    def test_rejects_bare_names(self):
        for actor in ("ada", "", "some agent", None):
            self.assertFalse(is_actor(actor), actor)


class TestFenceMasking(unittest.TestCase):
    def test_preserves_offsets(self):
        text = "a\n```\n# not a heading\n```\nb\n"
        self.assertEqual(len(mask_fenced_code(text)), len(text))

    def test_blanks_fenced_content(self):
        masked = mask_fenced_code("```\n# inside\n```\n# outside\n")
        self.assertNotIn("inside", masked)
        self.assertIn("# outside", masked)


class TestConcept(unittest.TestCase):
    def setUp(self):
        self.bundle = Bundle.load(REFERENCE)
        self.by_id = {concept.concept_id: concept for concept in self.bundle.concepts}

    def test_loads_the_reference_bundle(self):
        self.assertEqual(len(self.bundle.concepts), 5)
        self.assertEqual(len(self.bundle.logs), 1)
        self.assertEqual(self.bundle.declared_version, "0.2")

    def test_concept_id_strips_the_suffix(self):
        self.assertIn("metrics/deploy-success-rate", self.by_id)

    def test_trust_tier_prefers_human(self):
        computation = self.by_id["computations/deploy-success-rate"]
        self.assertEqual(computation.trust_tier, "human-reviewed")
        self.assertEqual(len(computation.verified), 2)

    def test_verified_bare_mapping_reads_as_a_list(self):
        service = self.by_id["services/deploy-orchestrator"]
        self.assertEqual(len(service.verified), 1)
        self.assertEqual(service.trust_tier, "human-reviewed")

    def test_verified_at_is_the_latest_event(self):
        computation = self.by_id["computations/deploy-success-rate"]
        self.assertEqual(format_timestamp(computation.verified_at), "2026-08-27T02:00:00Z")

    def test_status_defaults_to_stable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "a.md", "---\ntype: Metric\n---\n\n# X\n")
            self.assertEqual(Bundle.load(root).concepts[0].status, "stable")

    def test_staleness_is_a_comparison(self):
        metric = self.by_id["metrics/deploy-success-rate"]
        self.assertFalse(metric.is_stale(parse_timestamp("2026-08-28T00:00:00Z")))
        self.assertTrue(metric.is_stale(parse_timestamp("2027-01-01T00:00:00Z")))

    def test_extracts_the_computation_fence(self):
        computation = self.by_id["computations/deploy-success-rate"].computation()
        self.assertIn("deploy_success_rate", computation)
        self.assertIn("is_retry = false", computation)

    def test_links_resolve_bundle_relative(self):
        metric = self.by_id["metrics/deploy-success-rate"]
        targets = {link.target for link in metric.links()}
        self.assertIn("/computations/deploy-success-rate.md", targets)
        for link in metric.links():
            if not link.is_external:
                self.assertTrue(metric.resolve(link.target).exists(), link.target)

    def test_footnote_references_exclude_definitions(self):
        metric = self.by_id["metrics/deploy-success-rate"]
        self.assertEqual(set(metric.footnote_references()), {"slo-doc"})
        self.assertEqual(set(metric.footnote_definitions()), {"slo-doc"})

    def test_chunks_split_by_heading(self):
        metric = self.by_id["metrics/deploy-success-rate"]
        headings = [chunk.heading for chunk in metric.chunks()]
        self.assertEqual(headings, ["Definition", "Caveats", "Related"])

    def test_chunks_ignore_headings_inside_fences(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root,
                "a.md",
                "---\ntype: Metric\n---\n\n# Real\n\n```\n# Fake\n```\n",
            )
            chunks = Bundle.load(root).concepts[0].chunks()
            self.assertEqual([chunk.heading for chunk in chunks], ["Real"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
