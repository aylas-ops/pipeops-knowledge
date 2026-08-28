"""Generate draft OKF concepts from GitHub repository metadata.

Implements the manual's §6 pipeline for the GitHub source:

    metadata source -> extractor -> generated OKF markdown -> pull request
    -> owner review -> merge -> index for search and agents

Two decisions are deliberate and worth keeping:

1. **Everything is emitted as `status: draft`.** Generated metadata is not
   documentation (manual §14). Drafts are excluded from retrieval by default,
   so nothing an agent cites has skipped human review.

2. **`owner` is left blank unless CODEOWNERS says otherwise.** That makes the
   generated pull request fail CI on the `required_fields` rule, which is the
   point: assigning an accountable owner is the one thing a machine cannot
   infer, and a red check is what gets it done. Pass `--default-owner` to opt
   out when bulk-importing.

Auth: `GITHUB_TOKEN` / `GH_TOKEN`, else the token from `gh auth token`.
Dependency-free -- urllib only.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .model import format_timestamp, now_utc, parse_timestamp, render_frontmatter, slugify

API_ROOT = "https://api.github.com"
PRODUCER = "process:extract_github"

ADR_DIRECTORIES = (
    "docs/adr",
    "docs/adrs",
    "docs/decisions",
    "docs/architecture/decisions",
    "doc/adr",
    "adr",
)
ADR_SKIP = {"readme.md", "index.md", "template.md", "0000-template.md", "adr-template.md"}


# --------------------------------------------------------------------------
# GitHub client
# --------------------------------------------------------------------------


class GitHubError(RuntimeError):
    pass


def gh_available() -> bool:
    return shutil.which("gh") is not None


def resolve_token(explicit: str | None = None) -> str | None:
    """Find a token for the HTTP transport. None is fine when `gh` will be used."""
    if explicit:
        return explicit
    for variable in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(variable)
        if value:
            return value
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=15, check=True
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _ssl_context():
    """Prefer certifi's bundle when present.

    A python.org interpreter on macOS ships without a usable CA store until
    someone runs `Install Certificates.command`, which turns every request
    into an opaque CERTIFICATE_VERIFY_FAILED. Using certifi when it happens to
    be installed, and falling back to the `gh` transport when it is not, means
    the extractor works on a fresh machine either way.
    """
    import ssl

    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


@dataclass
class GitHub:
    """GitHub API client with two transports.

    `gh` is preferred when the CLI is on PATH: it already holds the user's
    credentials, handles enterprise hosts, and sidesteps local TLS trust
    problems. Plain HTTPS is the fallback for environments without it.
    """

    token: str | None = None
    api_root: str = API_ROOT
    transport: str = "auto"

    def __post_init__(self) -> None:
        if self.transport == "auto":
            self.transport = "gh" if gh_available() else "http"
        if self.transport == "http" and not self.token:
            raise GitHubError(
                "no GitHub token for the http transport: set GITHUB_TOKEN, or "
                "install the `gh` CLI and run `gh auth login`"
            )

    def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        if self.transport == "gh":
            return self._request_gh(f"{path}{query}")
        return self._request_http(f"{self.api_root}{path}{query}")

    def _request_gh(self, path: str) -> Any:
        try:
            result = subprocess.run(
                ["gh", "api", "-H", "Accept: application/vnd.github+json", path.lstrip("/")],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GitHubError(f"gh api {path} failed: {exc}") from exc
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "Not Found" in stderr or "404" in stderr:
                return None
            raise GitHubError(f"gh api {path} failed: {stderr}")
        return json.loads(result.stdout or "null")

    def _request_http(self, url: str) -> Any:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "okf-extract-github/0.1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30, context=_ssl_context()) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise GitHubError(f"GET {url} -> HTTP {exc.code}: {exc.reason}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network dependent
            raise GitHubError(
                f"GET {url} failed: {exc.reason}. If this is a TLS trust error, "
                "install the `gh` CLI and re-run -- the extractor will use it "
                "as transport automatically."
            ) from exc

    def repo(self, full_name: str) -> dict[str, Any] | None:
        return self._request(f"/repos/{full_name}")

    def org_repos(self, org: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        repos: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self._request(
                f"/orgs/{org}/repos", {"per_page": 100, "page": page, "sort": "full_name"}
            )
            if not batch:
                break
            repos.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        if not include_archived:
            repos = [repo for repo in repos if not repo.get("archived")]
        return repos

    def readme(self, full_name: str) -> dict[str, Any] | None:
        return self._request(f"/repos/{full_name}/readme")

    def contents(self, full_name: str, path: str) -> Any:
        return self._request(f"/repos/{full_name}/contents/{urllib.parse.quote(path)}")

    def last_commit_date(self, full_name: str, path: str) -> str | None:
        commits = self._request(f"/repos/{full_name}/commits", {"path": path, "per_page": 1})
        if not commits:
            return None
        raw = commits[0].get("commit", {}).get("committer", {}).get("date")
        if not raw:
            return None
        try:
            return format_timestamp(parse_timestamp(raw))
        except ValueError:
            return None


def decode_content(payload: dict[str, Any]) -> str:
    if payload.get("encoding") == "base64":
        return base64.b64decode(payload.get("content", "")).decode("utf-8", errors="replace")
    return payload.get("content", "") or ""


# --------------------------------------------------------------------------
# content shaping
# --------------------------------------------------------------------------


_BADGE_LINE = re.compile(r"^\s*(?:\[!\[|!\[|<img|<p align|</p>|<h1|<div align)")
_HEADING = re.compile(r"^#{1,6}\s")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def readme_summary(markdown: str, *, max_paragraphs: int = 2) -> str:
    """Pull the first real prose out of a README, skipping badges and titles."""
    text = _HTML_COMMENT.sub("", markdown)
    paragraphs: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line.strip():
            if current:
                paragraphs.append(" ".join(current).strip())
                current = []
            continue
        if _HEADING.match(line) or _BADGE_LINE.match(line):
            if current:
                paragraphs.append(" ".join(current).strip())
                current = []
            continue
        current.append(line.strip())
    if current:
        paragraphs.append(" ".join(current).strip())

    kept = [p for p in paragraphs if len(p) > 40][:max_paragraphs]
    return "\n\n".join(kept)


def first_sentence(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    if not text:
        return ""
    match = re.search(r"(?<=[.!?])\s", text)
    sentence = text[: match.start()].rstrip() if match else text
    if len(sentence) > limit:
        sentence = sentence[: limit - 1].rsplit(" ", 1)[0] + "…"
    return sentence


_CODEOWNER = re.compile(r"^\s*(?P<pattern>\S+)\s+(?P<owners>.+?)\s*$")


def parse_codeowners(text: str) -> list[tuple[str, list[str]]]:
    rules: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _CODEOWNER.match(stripped)
        if not match:
            continue
        owners = [token for token in match.group("owners").split() if token.startswith("@")]
        if owners:
            rules.append((match.group("pattern"), owners))
    return rules


def codeowner_to_actor(handle: str) -> str:
    """`@org/platform` -> `team:platform`; `@alice` -> `human:alice`."""
    handle = handle.lstrip("@")
    if "/" in handle:
        return f"team:{handle.split('/', 1)[1]}"
    return f"human:{handle}"


# --------------------------------------------------------------------------
# concept generation
# --------------------------------------------------------------------------

REVIEW_CHECKLIST = """# Owner review

> Generated by `{producer}`. Generated metadata is not documentation -- the
> nuance below is what a machine cannot infer, and is why this concept is
> still `status: draft`. Answer these, delete this block, set `status: stable`,
> and add a `verified` entry with your own `human:` actor.
>
> - What is this service actually for, in one sentence a new joiner understands?
> - What should an engineer or agent avoid doing to it?
> - What depends on it, and what does it depend on?
> - What do people most commonly get wrong about it?
> - When should this document be reviewed again? (set `stale_after`)
"""


def service_concept(
    repo: dict[str, Any],
    *,
    readme_markdown: str | None,
    readme_url: str | None,
    readme_modified: str | None,
    owner: str | None,
    generated_at: str,
) -> str:
    full_name = repo["full_name"]
    summary = readme_summary(readme_markdown or "")
    description = (
        repo.get("description")
        or first_sentence(summary)
        or f"Service repository {full_name}."
    )

    tags = sorted({*(repo.get("topics") or []), "service"})
    language = repo.get("language")
    if language:
        tags = sorted({*tags, language.lower()})

    frontmatter: dict[str, Any] = {
        "type": "Service",
        "title": repo.get("name", full_name),
        "description": first_sentence(description),
        "resource": repo["html_url"],
    }
    if owner:
        frontmatter["owner"] = owner
    frontmatter["tags"] = tags
    frontmatter["status"] = "draft"
    frontmatter["generated"] = {"by": PRODUCER, "at": generated_at}

    sources: list[dict[str, Any]] = [
        {
            "id": "repo",
            "resource": repo["html_url"],
            "title": f"{full_name} repository",
            "last_modified": _iso_or_none(repo.get("pushed_at")),
        }
    ]
    if readme_url:
        sources.append(
            {
                "id": "readme",
                "resource": readme_url,
                "title": f"{full_name} README",
                "last_modified": readme_modified,
            }
        )
    frontmatter["sources"] = [
        {k: v for k, v in entry.items() if v is not None} for entry in sources
    ]

    facts = [
        ("Default branch", f"`{repo.get('default_branch', '?')}`"),
        ("Visibility", "private" if repo.get("private") else "public"),
        ("Primary language", language or "—"),
        ("Last push", _iso_or_none(repo.get("pushed_at")) or "—"),
        ("Open issues", str(repo.get("open_issues_count", 0))),
        ("Homepage", repo.get("homepage") or "—"),
    ]

    if summary:
        # Bind the prose to the README source so the claim is attributable
        # (SPEC §5.1) rather than floating.
        opening = summary + ("[^readme]" if readme_url else "[^repo]")
    else:
        opening = (
            f"No README prose was found in [{full_name}]({repo['html_url']}). "
            "The owning team should replace this with one sentence explaining "
            "what this service does.[^repo]"
        )

    body_parts = [
        "# Summary",
        "",
        opening,
        "",
        "# Repository facts",
        "",
        "| Fact | Value |",
        "|------|-------|",
        *[f"| {label} | {value} |" for label, value in facts],
        "",
        "Extracted from the repository metadata.[^repo]",
        "",
        REVIEW_CHECKLIST.format(producer=PRODUCER),
        "",
        f"[^repo]: {full_name} repository",
    ]
    if readme_url:
        body_parts.append(f"[^readme]: {full_name} README")

    return render_frontmatter(frontmatter) + "\n" + "\n".join(body_parts).rstrip() + "\n"


def decision_concept(
    repo: dict[str, Any],
    *,
    filename: str,
    markdown: str,
    html_url: str,
    last_modified: str | None,
    owner: str | None,
    generated_at: str,
) -> tuple[str, str]:
    """Return `(slug, document)` for one architecture decision record."""
    heading = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    stem = Path(filename).stem
    title = heading.group(1).strip() if heading else stem.replace("-", " ").replace("_", " ").title()

    body_text = markdown[heading.end() :] if heading else markdown
    description = first_sentence(readme_summary(body_text, max_paragraphs=1)) or first_sentence(title)

    status_match = re.search(
        r"^\s*(?:##\s*)?status\s*[:\-]?\s*(accepted|proposed|superseded|rejected|deprecated)",
        markdown,
        re.IGNORECASE | re.MULTILINE,
    )
    adr_status = status_match.group(1).lower() if status_match else None

    frontmatter: dict[str, Any] = {
        "type": "Decision",
        "title": title,
        "description": description,
        "resource": html_url,
    }
    if owner:
        frontmatter["owner"] = owner
    frontmatter["tags"] = sorted({"adr", "decision", repo["name"].lower()})
    frontmatter["status"] = "draft"
    if adr_status:
        # Not an OKF field; kept because the ADR's own lifecycle is real
        # information and the spec preserves unknown keys (§4.1).
        frontmatter["adr_status"] = adr_status
    frontmatter["generated"] = {"by": PRODUCER, "at": generated_at}
    frontmatter["sources"] = [
        {
            k: v
            for k, v in {
                "id": "adr",
                "resource": html_url,
                "title": f"{repo['full_name']}: {filename}",
                "last_modified": last_modified,
            }.items()
            if v is not None
        }
    ]

    body = "\n".join(
        [
            "# Decision",
            "",
            f"Recorded in [{repo['full_name']}/{filename}]({html_url}).[^adr]",
            "",
            (body_text.strip()[:4000] or "*(no body captured)*"),
            "",
            f"# Service",
            "",
            f"Applies to [{repo['name']}](../services/{slugify(repo['name'])}.md).",
            "",
            "[^adr]: " + f"{repo['full_name']}: {filename}",
        ]
    )

    slug = f"{slugify(repo['name'])}--{slugify(stem)}"
    return slug, render_frontmatter(frontmatter) + "\n" + body.rstrip() + "\n"


def _iso_or_none(value: Any) -> str | None:
    if not value:
        return None
    try:
        return format_timestamp(parse_timestamp(value))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# extraction run
# --------------------------------------------------------------------------


@dataclass
class ExtractionResult:
    written: list[Path]
    skipped: list[Path]
    unowned: list[str]


def extract(
    client: GitHub,
    repos: Sequence[dict[str, Any]],
    bundle_root: Path,
    *,
    default_owner: str | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    include_decisions: bool = True,
) -> ExtractionResult:
    generated_at = format_timestamp(now_utc())
    written: list[Path] = []
    skipped: list[Path] = []
    unowned: list[str] = []

    services_dir = bundle_root / "services"
    decisions_dir = bundle_root / "decisions"

    for repo in repos:
        full_name = repo["full_name"]
        owner = _repo_owner(client, full_name) or default_owner
        if not owner:
            unowned.append(full_name)

        readme_payload = client.readme(full_name)
        readme_markdown = decode_content(readme_payload) if readme_payload else None
        readme_url = readme_payload.get("html_url") if readme_payload else None
        readme_modified = (
            client.last_commit_date(full_name, readme_payload["path"]) if readme_payload else None
        )

        document = service_concept(
            repo,
            readme_markdown=readme_markdown,
            readme_url=readme_url,
            readme_modified=readme_modified,
            owner=owner,
            generated_at=generated_at,
        )
        target = services_dir / f"{slugify(repo['name'])}.md"
        _emit(target, document, overwrite=overwrite, dry_run=dry_run, written=written, skipped=skipped)

        if not include_decisions:
            continue
        for filename, markdown, html_url, path in _iter_adrs(client, full_name):
            slug, adr_document = decision_concept(
                repo,
                filename=filename,
                markdown=markdown,
                html_url=html_url,
                last_modified=client.last_commit_date(full_name, path),
                owner=owner,
                generated_at=generated_at,
            )
            _emit(
                decisions_dir / f"{slug}.md",
                adr_document,
                overwrite=overwrite,
                dry_run=dry_run,
                written=written,
                skipped=skipped,
            )

    return ExtractionResult(written=written, skipped=skipped, unowned=unowned)


def _repo_owner(client: GitHub, full_name: str) -> str | None:
    for path in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        payload = client.contents(full_name, path)
        if not payload or isinstance(payload, list):
            continue
        rules = parse_codeowners(decode_content(payload))
        if not rules:
            continue
        # Prefer the catch-all rule; fall back to the first rule declared.
        for pattern, owners in rules:
            if pattern in ("*", "/*", "**"):
                return codeowner_to_actor(owners[0])
        return codeowner_to_actor(rules[0][1][0])
    return None


def _iter_adrs(client: GitHub, full_name: str) -> Iterable[tuple[str, str, str, str]]:
    for directory in ADR_DIRECTORIES:
        listing = client.contents(full_name, directory)
        if not isinstance(listing, list):
            continue
        for entry in listing:
            if entry.get("type") != "file" or not entry["name"].endswith(".md"):
                continue
            if entry["name"].lower() in ADR_SKIP:
                continue
            payload = client.contents(full_name, entry["path"])
            if not payload or isinstance(payload, list):
                continue
            yield entry["name"], decode_content(payload), entry["html_url"], entry["path"]
        return  # only the first ADR directory that exists


def _emit(
    target: Path,
    document: str,
    *,
    overwrite: bool,
    dry_run: bool,
    written: list[Path],
    skipped: list[Path],
) -> None:
    if target.exists() and not overwrite:
        skipped.append(target)
        return
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document, encoding="utf-8")
    written.append(target)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="okf extract-github",
        description="Generate draft OKF concepts from GitHub repository metadata.",
    )
    parser.add_argument("--org", help="extract every non-archived repo in this organisation")
    parser.add_argument(
        "--repo", action="append", default=[], metavar="OWNER/NAME", help="repeatable"
    )
    parser.add_argument("--bundle", required=True, type=Path, help="bundle root to write into")
    parser.add_argument(
        "--default-owner",
        help="owner to use when CODEOWNERS names none (e.g. team:platform). "
        "Omitted by default so CI forces a human to assign one.",
    )
    parser.add_argument("--overwrite", action="store_true", help="replace existing concept files")
    parser.add_argument("--no-decisions", action="store_true", help="skip ADR extraction")
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--token", help="GitHub token (defaults to env or `gh auth token`)")
    parser.add_argument(
        "--transport",
        choices=("auto", "gh", "http"),
        default="auto",
        help="auto prefers the `gh` CLI when installed",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.org and not args.repo:
        print("error: pass --org or at least one --repo", file=sys.stderr)
        return 2

    client = GitHub(token=resolve_token(args.token), transport=args.transport)

    repos: list[dict[str, Any]] = []
    if args.org:
        repos.extend(client.org_repos(args.org, include_archived=args.include_archived))
    for full_name in args.repo:
        repo = client.repo(full_name)
        if repo is None:
            print(f"warning: {full_name} not found or not visible to this token", file=sys.stderr)
            continue
        repos.append(repo)

    if not repos:
        print("error: no repositories resolved", file=sys.stderr)
        return 1

    result = extract(
        client,
        repos,
        args.bundle,
        default_owner=args.default_owner,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        include_decisions=not args.no_decisions,
    )

    prefix = "would write" if args.dry_run else "wrote"
    for path in result.written:
        print(f"{prefix}: {path}")
    for path in result.skipped:
        print(f"skipped (exists, use --overwrite): {path}")

    print(
        f"\n{len(result.written)} concept(s) {prefix}, {len(result.skipped)} skipped, "
        f"from {len(repos)} repo(s)."
    )
    if result.unowned:
        print(
            f"\n{len(result.unowned)} repo(s) had no CODEOWNERS entry, so their concepts "
            "carry no `owner` and `okf validate` will fail until a human assigns one:"
        )
        for full_name in result.unowned:
            print(f"  - {full_name}")
    if not args.dry_run:
        print("\nNext: `okf reindex`, then `okf validate`, then open a review PR.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
