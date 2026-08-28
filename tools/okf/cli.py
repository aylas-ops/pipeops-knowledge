"""`okf` command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .model import (
    Bundle,
    discover_bundles,
    format_timestamp,
    now_utc,
    parse_timestamp,
    render_frontmatter,
    slugify,
)
from .profile import ERROR, Profile, ProfileError

DEFAULT_BUNDLES = Path("bundles")
DEFAULT_DB = Path("okf-index.okf.db")


def _load_bundles(path: Path) -> list[Bundle]:
    if not path.exists():
        raise SystemExit(f"error: {path} does not exist")
    bundles = discover_bundles(path)
    if not bundles:
        raise SystemExit(f"error: no markdown found under {path}")
    return bundles


def _profile(args: argparse.Namespace, start: Path) -> Profile:
    try:
        return Profile.discover(start, explicit=getattr(args, "profile", None))
    except ProfileError as exc:
        raise SystemExit(f"error: {exc}")


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    from .validate import render, summarize, validate_bundles

    bundles = _load_bundles(args.path)
    profile = _profile(args, args.path)
    now = parse_timestamp(args.now) if args.now else now_utc()

    findings = validate_bundles(bundles, profile, now=now)
    if findings:
        print(render(findings, args.format, root=Path.cwd()))

    counts = summarize(findings)
    if args.format != "json":
        concepts = sum(len(bundle.concepts) for bundle in bundles)
        print(
            f"\n{len(bundles)} bundle(s), {concepts} concept(s) checked against "
            f"profile {profile.name!r} (OKF v{profile.okf_version}): "
            f"{counts.get(ERROR, 0)} error(s), {counts.get('warning', 0)} warning(s)."
        )

    if counts.get(ERROR, 0):
        return 1
    if args.strict and counts.get("warning", 0):
        return 1
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    from .index import build

    bundles = _load_bundles(args.path)
    counts = build(bundles, args.db, include_drafts=not args.exclude_drafts)
    print(
        f"indexed {counts['concepts']} concept(s) into {counts['chunks']} chunk(s) "
        f"from {counts['bundles']} bundle(s) -> {args.db}"
    )
    print(f"  {counts['links']} link(s), {counts['sources']} source record(s)")
    if args.exclude_drafts:
        print("  drafts excluded at index time")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    from .query import render_results, search

    if not Path(args.db).exists():
        raise SystemExit(f"error: no index at {args.db}. Run `okf index` first.")

    results = search(
        args.db,
        args.text,
        types=args.type,
        tags=args.tag,
        owner=args.owner,
        bundle=args.bundle,
        include_drafts=args.include_drafts,
        include_stale=args.include_stale,
        include_deprecated=args.include_deprecated,
        limit=args.limit,
    )
    print(render_results(results, args.format))
    return 0 if results else 1


def cmd_get(args: argparse.Namespace) -> int:
    from .query import get_concept

    record = get_concept(args.db, args.concept_id)
    if record is None:
        print(f"not found: {args.concept_id}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(record, indent=2, default=str))
    else:
        print(f"{record['concept_id']}  [{record['type']}]")
        print(f"  title       {record['title']}")
        print(f"  owner       {record['owner']}")
        print(f"  status      {record['status']}  (trust: {record['trust_tier']})")
        print(f"  resource    {record['resource']}")
        print(f"  stale_after {record['stale_after']}  stale={bool(record['is_stale'])}")
        print(f"  linked from {len(record['links_in'])}, links to {len(record['links_out'])}")
        print()
        print(record["body"])
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    from .index import stats

    if not Path(args.db).exists():
        raise SystemExit(f"error: no index at {args.db}. Run `okf index` first.")
    print(json.dumps(stats(args.db), indent=2, default=str))
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    from .indexgen import write_indexes

    bundles = _load_bundles(args.path)
    profile = _profile(args, args.path)
    changed = 0
    for bundle in bundles:
        for path, was_changed in write_indexes(
            bundle, okf_version=profile.okf_version, dry_run=args.dry_run
        ):
            if was_changed:
                changed += 1
                verb = "would update" if args.dry_run else "updated"
                print(f"{verb}: {path}")
    print(f"\n{changed} index file(s) {'would change' if args.dry_run else 'changed'}.")
    return 1 if (args.dry_run and changed and args.check) else 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .serve import run

    run(Path(args.db), host=args.host, port=args.port)
    return 0


def cmd_extract_github(args: argparse.Namespace, extra: Sequence[str]) -> int:
    from .extract_github import main as extract_main

    return extract_main(list(extra))


_TEMPLATES: dict[str, dict[str, object]] = {
    "Service": {"tags": ["service"], "sections": ["Summary", "Interfaces", "Dependencies", "Operational notes"]},
    "Metric": {"tags": ["metric"], "sections": ["Definition", "Caveats", "Related"]},
    "Playbook": {"tags": ["oncall"], "sections": ["Trigger", "Steps", "Escalation"]},
    "Runbook": {"tags": ["oncall"], "sections": ["Trigger", "Steps", "Escalation"]},
    "Dataset": {"tags": ["data"], "sections": ["Schema", "Caveats", "Related"]},
    "Decision": {"tags": ["decision"], "sections": ["Context", "Decision", "Consequences"]},
    "Attested Computation": {
        "tags": ["attested"],
        "sections": ["Computation", "What the attester checks", "Freshness"],
    },
}


def cmd_new(args: argparse.Namespace) -> int:
    # Discover from the working directory, not the output path: `okf new` is
    # often pointed at a scratch location outside any repository, and the
    # house profile is what tells us which fields the template must include.
    profile = _profile(args, Path.cwd())
    template = _TEMPLATES.get(args.type, {"tags": [], "sections": ["Summary"]})

    frontmatter: dict[str, object] = {
        "type": args.type,
        "title": args.title,
        "description": args.description or "TODO: one sentence a new joiner understands.",
        "owner": args.owner or "team:unassigned",
        "tags": sorted({*template["tags"], *(args.tag or [])}),  # type: ignore[index]
        "status": "draft",
        "generated": {"by": args.by, "at": format_timestamp(now_utc())},
    }
    if args.resource:
        frontmatter["resource"] = args.resource
    if args.type in profile.stale_after_required_types:
        frontmatter["stale_after"] = "TODO: ISO 8601 datetime with a UTC offset"
    if args.type == "Attested Computation":
        frontmatter["runtime"] = "TODO: bigquery | postgres | dbt | python"
        frontmatter["parameters"] = []

    body = "\n\n".join(f"# {section}\n\nTODO" for section in template["sections"])  # type: ignore[union-attr]
    document = render_frontmatter(frontmatter) + "\n" + body + "\n"

    target = args.out or (args.path / f"{slugify(args.title)}.md")
    if target.exists() and not args.force:
        raise SystemExit(f"error: {target} exists (use --force)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding="utf-8")
    print(f"created {target}")
    print("Fill in the TODOs, then `okf reindex && okf validate`.")
    return 0


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="okf", description="OKF v0.2 bundle tooling.")
    parser.add_argument("--version", action="version", version=f"okf {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_path(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "path", nargs="?", type=Path, default=DEFAULT_BUNDLES, help="bundle root or directory of bundles"
        )

    def add_db(target: argparse.ArgumentParser) -> None:
        target.add_argument("--db", type=Path, default=DEFAULT_DB, help="index database path")

    validate = sub.add_parser("validate", help="check conformance and house rules")
    add_path(validate)
    validate.add_argument("--profile", type=Path, help="path to okf-profile.yaml")
    validate.add_argument("--format", choices=("text", "json", "github"), default="text")
    validate.add_argument("--strict", action="store_true", help="treat warnings as failures")
    validate.add_argument("--now", help="ISO timestamp to evaluate freshness against")
    validate.set_defaults(func=cmd_validate)

    index = sub.add_parser("index", help="build the retrieval index")
    add_path(index)
    add_db(index)
    index.add_argument("--exclude-drafts", action="store_true")
    index.set_defaults(func=cmd_index)

    query = sub.add_parser("query", help="search the index")
    query.add_argument("text")
    add_db(query)
    query.add_argument("--type", action="append", help="filter by concept type (repeatable)")
    query.add_argument("--tag", action="append", help="filter by tag (repeatable)")
    query.add_argument("--owner")
    query.add_argument("--bundle")
    query.add_argument("--limit", type=int, default=8)
    query.add_argument("--include-drafts", action="store_true")
    query.add_argument("--include-stale", action="store_true")
    query.add_argument("--include-deprecated", action="store_true")
    query.add_argument(
        "--format",
        choices=("text", "json", "context"),
        default="text",
        help="`context` emits a grounding block for an agent prompt",
    )
    query.set_defaults(func=cmd_query)

    get = sub.add_parser("get", help="print one concept from the index")
    get.add_argument("concept_id")
    add_db(get)
    get.add_argument("--format", choices=("text", "json"), default="text")
    get.set_defaults(func=cmd_get)

    stats = sub.add_parser("stats", help="index coverage and trust breakdown")
    add_db(stats)
    stats.set_defaults(func=cmd_stats)

    reindex = sub.add_parser("reindex", help="regenerate index.md files (SPEC §8)")
    add_path(reindex)
    reindex.add_argument("--profile", type=Path)
    reindex.add_argument("--dry-run", action="store_true")
    reindex.add_argument("--check", action="store_true", help="with --dry-run, exit 1 if stale")
    reindex.set_defaults(func=cmd_reindex)

    serve = sub.add_parser("serve", help="localhost retrieval endpoint for agent tools")
    add_db(serve)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.set_defaults(func=cmd_serve)

    new = sub.add_parser("new", help="scaffold a concept from a template")
    new.add_argument("type", choices=sorted(_TEMPLATES))
    new.add_argument("title")
    new.add_argument("--path", type=Path, default=Path("."), help="directory to write into")
    new.add_argument("--out", type=Path, help="explicit output file")
    new.add_argument("--description")
    new.add_argument("--owner")
    new.add_argument("--resource")
    new.add_argument("--tag", action="append")
    new.add_argument("--by", default="human:unknown", help="actor for `generated.by` (SPEC §7)")
    new.add_argument("--profile", type=Path)
    new.add_argument("--force", action="store_true")
    new.set_defaults(func=cmd_new)

    extract = sub.add_parser(
        "extract-github",
        help="generate draft concepts from GitHub metadata",
        add_help=False,
    )
    extract.set_defaults(func=None, passthrough=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "extract-github":
        return cmd_extract_github(argparse.Namespace(), argv[1:])

    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
