"""Tests for the GitHub extractor and index.md generation.

Network calls are not exercised; the value is in the shaping. The load-bearing
assertion is that a generated concept parses and validates as far as it should
-- clean except for the `owner` a human still has to assign.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from okf.extract_github import (
    codeowner_to_actor,
    decision_concept,
    first_sentence,
    parse_codeowners,
    readme_summary,
    service_concept,
)
from okf.indexgen import prose_sections, render_index, write_indexes
from okf.model import Bundle, parse_timestamp, split_frontmatter
from okf.profile import ERROR, Profile
from okf.validate import summarize, validate_bundle

REPO = Path(__file__).resolve().parent.parent
REFERENCE = REPO / "bundles" / "reference_example"
NOW = "2026-08-28T12:00:00Z"

REPO_PAYLOAD = {
    "full_name": "example-org/billing-api",
    "name": "billing-api",
    "html_url": "https://github.com/example-org/billing-api",
    "description": "Usage metering and invoice generation.",
    "default_branch": "main",
    "private": True,
    "language": "Go",
    "topics": ["billing", "api"],
    "pushed_at": "2026-08-01T10:00:00Z",
    "open_issues_count": 4,
    "homepage": None,
}

README = """# billing-api

[![CI](https://img.shields.io/badge/ci-passing-green)](https://ci.example.org)

The billing API meters customer resource usage and turns it into invoices. It
owns the authoritative usage ledger; every other service reads from it.

```go
func main() {}
```

## Development

Run `make dev`.
"""


class TestReadmeShaping(unittest.TestCase):
    def test_skips_badges_headings_and_code(self):
        summary = readme_summary(README)
        self.assertTrue(summary.startswith("The billing API meters"))
        self.assertNotIn("shields.io", summary)
        self.assertNotIn("func main", summary)
        self.assertNotIn("Run `make dev`", summary)

    def test_first_sentence_stops_at_the_period(self):
        self.assertEqual(first_sentence("One. Two."), "One.")

    def test_first_sentence_truncates_a_runon(self):
        self.assertTrue(first_sentence("x " * 200).endswith("…"))

    def test_empty_readme_yields_empty_summary(self):
        self.assertEqual(readme_summary(""), "")


class TestCodeowners(unittest.TestCase):
    def test_parses_rules_and_skips_comments(self):
        rules = parse_codeowners("# comment\n\n*  @example-org/platform\n/docs @alice\n")
        self.assertEqual(rules[0], ("*", ["@example-org/platform"]))
        self.assertEqual(rules[1], ("/docs", ["@alice"]))

    def test_maps_handles_to_actors(self):
        self.assertEqual(codeowner_to_actor("@example-org/platform"), "team:platform")
        self.assertEqual(codeowner_to_actor("@alice"), "human:alice")


class TestGeneratedConcepts(unittest.TestCase):
    def build(self, owner: str | None):
        return service_concept(
            REPO_PAYLOAD,
            readme_markdown=README,
            readme_url="https://github.com/example-org/billing-api/blob/main/README.md",
            readme_modified="2026-07-30T00:00:00Z",
            owner=owner,
            generated_at=NOW,
        )

    def test_generated_frontmatter_parses(self):
        frontmatter, body, _ = split_frontmatter(self.build("team:platform"))
        self.assertEqual(frontmatter["type"], "Service")
        self.assertEqual(frontmatter["owner"], "team:platform")
        self.assertEqual(frontmatter["generated"]["by"], "process:extract_github")
        self.assertIn("billing", frontmatter["tags"])
        self.assertIn("go", frontmatter["tags"])
        self.assertIn("# Repository facts", body)

    def test_generated_content_is_always_a_draft(self):
        frontmatter, _, _ = split_frontmatter(self.build("team:platform"))
        self.assertEqual(frontmatter["status"], "draft")

    def test_owner_is_omitted_when_codeowners_names_none(self):
        frontmatter, _, _ = split_frontmatter(self.build(None))
        self.assertNotIn("owner", frontmatter)

    def test_footnotes_bind_to_declared_sources(self):
        frontmatter, body, _ = split_frontmatter(self.build("team:platform"))
        declared = {entry["id"] for entry in frontmatter["sources"]}
        self.assertEqual(declared, {"repo", "readme"})
        for label in declared:
            self.assertIn(f"[^{label}]:", body)

    def test_review_checklist_asks_the_manual_s_questions(self):
        _, body, _ = split_frontmatter(self.build("team:platform"))
        self.assertIn("Owner review", body)
        self.assertIn("should an engineer or agent avoid", body)

    def test_a_generated_bundle_validates_except_for_the_owner(self):
        """The one deliberate failure is the one a human has to resolve."""
        profile = Profile.load(REPO / "okf-profile.yaml")
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "generated"
            (root / "services").mkdir(parents=True)
            (root / "services" / "billing-api.md").write_text(self.build(None), encoding="utf-8")
            write_indexes(Bundle.load(root))

            findings = validate_bundle(
                Bundle.load(root), profile, now=parse_timestamp(NOW)
            )
            errors = [finding for finding in findings if finding.severity == ERROR]
            self.assertEqual(
                {finding.rule for finding in errors},
                {"required_fields"},
                "\n".join(finding.format(root) for finding in errors),
            )
            self.assertIn("owner", errors[0].message)

    def test_decision_concept_extracts_title_and_adr_status(self):
        slug, document = decision_concept(
            REPO_PAYLOAD,
            filename="0003-use-postgres.md",
            markdown="# Use Postgres for the ledger\n\n## Status\n\nAccepted\n\nWe need ACID.\n",
            html_url="https://github.com/example-org/billing-api/blob/main/docs/adr/0003.md",
            last_modified="2026-05-01T00:00:00Z",
            owner="team:platform",
            generated_at=NOW,
        )
        frontmatter, body, _ = split_frontmatter(document)
        self.assertEqual(slug, "billing-api--0003-use-postgres")
        self.assertEqual(frontmatter["title"], "Use Postgres for the ledger")
        self.assertEqual(frontmatter["adr_status"], "accepted")
        self.assertIn("[^adr]:", body)


class TestIndexGeneration(unittest.TestCase):
    def test_reindexing_the_reference_bundle_is_a_no_op(self):
        """The committed indexes must match what the generator produces."""
        results = write_indexes(Bundle.load(REFERENCE), dry_run=True)
        changed = [path for path, was_changed in results if was_changed]
        self.assertEqual(changed, [], f"stale index files: {changed}")

    def test_root_index_declares_the_version(self):
        content = render_index(REFERENCE, Bundle.load(REFERENCE), okf_version="0.2")
        self.assertTrue(content.startswith('---\nokf_version: "0.2"\n---'))

    def test_prose_sections_survive_regeneration(self):
        content = render_index(
            REFERENCE,
            Bundle.load(REFERENCE),
            okf_version="0.2",
            prose=prose_sections(REFERENCE / "index.md"),
        )
        self.assertIn("# About this bundle", content)
        self.assertIn("reference example", content)

    def test_generated_entries_carry_descriptions(self):
        content = render_index(REFERENCE / "metrics", Bundle.load(REFERENCE))
        self.assertIn("# Metric", content)
        self.assertIn("Share of customer deployments", content)

    def test_drafts_are_marked_in_the_index(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.md").write_text(
                "---\ntype: Metric\ntitle: A\ndescription: A metric.\nstatus: draft\n---\n\n# X\n",
                encoding="utf-8",
            )
            bundle = Bundle.load(root)
            content = render_index(bundle.root, bundle)
            self.assertIn("awaiting owner review", content)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
