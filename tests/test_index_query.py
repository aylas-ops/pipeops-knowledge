"""Tests for indexing and retrieval.

The behaviour worth protecting here is the filtering. A retrieval layer that
happily serves a draft or an expired document is the manual's §14 pitfall
wearing a search box.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from okf.index import build, stats
from okf.model import Bundle, parse_timestamp
from okf.query import format_context, get_concept, search, to_match_query

REPO = Path(__file__).resolve().parent.parent
REFERENCE = REPO / "bundles" / "reference_example"
NOW = parse_timestamp("2026-08-28T12:00:00Z")


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def concept(title: str, *, status: str = "stable", stale_after: str | None = None) -> str:
    extra = f"stale_after: {stale_after}\n" if stale_after else ""
    return (
        f"---\ntype: Metric\ntitle: {title}\ndescription: A {title} metric.\n"
        f"owner: team:data\ntags: [widget]\nstatus: {status}\n{extra}---\n\n"
        f"# Definition\n\nThe {title} metric counts widgets shipped per fortnight.\n"
    )


class TestMatchQuery(unittest.TestCase):
    def test_drops_stopwords(self):
        self.assertEqual(to_match_query("why do the retries fail"), '"retries" OR "fail"')

    def test_falls_back_when_everything_is_a_stopword(self):
        self.assertEqual(to_match_query("what is it"), '"what" OR "is" OR "it"')

    def test_neutralises_fts_syntax(self):
        self.assertNotIn("(", to_match_query('rate NEAR("x") (y)'))

    def test_empty_query_is_safe(self):
        self.assertEqual(to_match_query("   "), '""')


class TestIndexBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = Path(self.tmp.name) / "index.db"
        self.counts = build([Bundle.load(REFERENCE)], self.db, now=NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def test_indexes_every_concept(self):
        self.assertEqual(self.counts["concepts"], 5)
        self.assertGreater(self.counts["chunks"], self.counts["concepts"])

    def test_records_links_and_sources(self):
        self.assertGreater(self.counts["links"], 0)
        self.assertGreater(self.counts["sources"], 0)
        self.assertEqual(stats(self.db)["broken_links"], 0)

    def test_rebuild_is_idempotent(self):
        again = build([Bundle.load(REFERENCE)], self.db, now=NOW)
        self.assertEqual(again, self.counts)

    def test_precomputes_trust_and_staleness(self):
        summary = stats(self.db)
        self.assertEqual(summary["by_trust"], {"human-reviewed": 5})
        self.assertEqual(summary["by_status"], {"stable": 5})

    def test_get_concept_resolves_a_suffix(self):
        record = get_concept(self.db, "metrics/deploy-success-rate")
        self.assertIsNotNone(record)
        self.assertEqual(record["type"], "Metric")
        self.assertTrue(record["links_in"])

    def test_search_finds_the_caveat_nobody_could_have_generated(self):
        results = search(self.db, "retries counted twice inflate the rate", limit=5)
        self.assertTrue(results)
        self.assertIn(
            "reference_example/metrics/deploy-success-rate",
            {result.concept_id for result in results},
        )

    def test_results_carry_a_citation(self):
        result = search(self.db, "escalation page platform", limit=1)[0]
        self.assertTrue(result.citation.endswith(".md#escalation") or ".md" in result.citation)

    def test_type_filter(self):
        results = search(self.db, "deployment", types=["Playbook"], limit=10)
        self.assertTrue(results)
        self.assertEqual({result.type for result in results}, {"Playbook"})

    def test_tag_filter(self):
        results = search(self.db, "deployment", tags=["oncall"], limit=10)
        self.assertTrue(results)
        for result in results:
            self.assertIn("oncall", result.tags)

    def test_owner_filter_excludes_everything_when_unmatched(self):
        self.assertEqual(search(self.db, "deployment", owner="team:nobody"), [])


class TestRetrievalFilters(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name) / "bundle"
        write(root, "index.md", '---\nokf_version: "0.2"\n---\n\n# Metric\n')
        write(root, "live.md", concept("Live", stale_after="2027-01-01T00:00:00Z"))
        write(root, "drafty.md", concept("Drafty", status="draft"))
        write(root, "expired.md", concept("Expired", stale_after="2026-01-01T00:00:00Z"))
        write(root, "old.md", concept("Old", status="deprecated"))
        self.db = Path(self.tmp.name) / "index.db"
        build([Bundle.load(root)], self.db, now=NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def titles(self, **kwargs) -> set[str]:
        return {result.title for result in search(self.db, "widgets fortnight", limit=20, **kwargs)}

    def test_drafts_stale_and_deprecated_are_excluded_by_default(self):
        self.assertEqual(self.titles(), {"Live"})

    def test_drafts_can_be_opted_into(self):
        self.assertEqual(self.titles(include_drafts=True), {"Live", "Drafty"})

    def test_stale_can_be_opted_into_and_is_labelled(self):
        results = search(self.db, "widgets fortnight", include_stale=True, limit=20)
        expired = next(result for result in results if result.title == "Expired")
        self.assertTrue(expired.is_stale)
        self.assertTrue(any("STALE" in warning for warning in expired.warnings))

    def test_deprecated_can_be_opted_into(self):
        self.assertIn("Old", self.titles(include_deprecated=True))

    def test_context_block_repeats_warnings_and_demands_citations(self):
        results = search(self.db, "widgets fortnight", include_drafts=True, limit=5)
        block = format_context(results)
        self.assertIn("citation:", block)
        self.assertIn("Cite every claim", block)
        self.assertIn("DRAFT", block)

    def test_empty_results_tell_the_agent_not_to_improvise(self):
        block = format_context([])
        self.assertIn("rather than answering from general knowledge", block)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
