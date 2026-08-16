#!/usr/bin/env python3
from __future__ import annotations

import calendar
import json
import math
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import quote, urlencode, urlsplit

from scripts.update_language_stats import (
    LANGUAGE_COLORS,
    aggregate_weights,
    displayed_languages,
    render_svg as render_language_card,
)


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
DEFAULT_USERNAME = os.environ.get("GITHUB_REPOSITORY_OWNER", "xzAscC")
GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL = f"{GITHUB_API}/graphql"
MAX_RESPONSE_BYTES = 1_000_000
LIGHT_ACCENT = "#0f766e"
VISIT_VALUE_PATTERN = re.compile(r"[0-9]+(?:[.,][0-9]+)*(?:[kKmMbB])?")


@dataclass(frozen=True, slots=True)
class Theme:
    background: str
    title: str
    text: str
    icon: str
    border: str
    ring: str


DARK_THEME = Theme(
    background="#0d1117",
    title="#5eead4",
    text="#94a3b8",
    icon="#f59e0b",
    border="#1e3a32",
    ring="#14b8a6",
)
LIGHT_THEME = Theme(
    background="#ffffff",
    title=LIGHT_ACCENT,
    text="#334155",
    icon="#d97706",
    border="#e2e8f0",
    ring=LIGHT_ACCENT,
)

STAT_ICONS = {
    "stars": (
        "M8 .25a.75.75 0 01.673.418l1.882 3.815 4.21.612a.75.75 0 01.416 1.279l-3.046 "
        "2.97.719 4.192a.75.75 0 01-1.088.791L8 12.347l-3.766 1.98a.75.75 0 "
        "01-1.088-.79l.72-4.194L.818 6.374a.75.75 0 01.416-1.28l4.21-.611L7.327.668A.75.75 "
        "0 018 .25zm0 2.445L6.615 5.5a.75.75 0 01-.564.41l-3.097.45 2.24 2.184a.75.75 0 "
        "01.216.664l-.528 3.084 2.769-1.456a.75.75 0 01.698 0l2.77 1.456-.53-3.084a.75.75 "
        "0 01.216-.664l2.24-2.183-3.096-.45a.75.75 0 01-.564-.41L8 2.694v.001z"
    ),
    "commits": (
        "M1.643 3.143L.427 1.927A.25.25 0 000 2.104V5.75c0 .138.112.25.25.25h3.646a.25.25 "
        "0 00.177-.427L2.715 4.215a6.5 6.5 0 11-1.18 4.458.75.75 0 10-1.493.154 8.001 "
        "8.001 0 101.6-5.684zM7.75 4a.75.75 0 01.75.75v2.992l2.028.812a.75.75 0 "
        "01-.557 1.392l-2.5-1A.75.75 0 017 8.25v-3.5A.75.75 0 017.75 4z"
    ),
    "prs": (
        "M7.177 3.073L9.573.677A.25.25 0 0110 .854v4.792a.25.25 0 01-.427.177L7.177 "
        "3.427a.25.25 0 010-.354zM3.75 2.5a.75.75 0 100 1.5.75.75 0 000-1.5zm-2.25.75a2.25 "
        "2.25 0 113 2.122v5.256a2.251 2.251 0 11-1.5 0V5.372A2.25 2.25 0 011.5 3.25zM11 "
        "2.5h-1V4h1a1 1 0 011 1v5.628a2.251 2.251 0 101.5 0V5A2.5 2.5 0 0011 2.5zm1 "
        "10.25a.75.75 0 111.5 0 .75.75 0 01-1.5 0zM3.75 12a.75.75 0 100 1.5.75.75 0 000-1.5z"
    ),
    "issues": (
        "M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM0 8a8 8 0 1116 0A8 8 0 010 8zm9 3a1 1 "
        "0 11-2 0 1 1 0 012 0zm-.25-6.25a.75.75 0 00-1.5 0v3.5a.75.75 0 001.5 0v-3.5z"
    ),
    "contribs": (
        "M2 2.5A2.5 2.5 0 014.5 0h8.75a.75.75 0 01.75.75v12.5a.75.75 0 01-.75.75h-2.5a.75.75 "
        "0 110-1.5h1.75v-2h-8a1 1 0 00-.714 1.7.75.75 0 01-1.072 1.05A2.495 2.495 0 012 "
        "11.5v-9zm10.5-1V9h-8c-.356 0-.694.074-1 .208V2.5a1 1 0 011-1h8zM5 12.25v3.25a.25.25 "
        "0 00.4.2l1.45-1.087a.25.25 0 01.3 0L8.6 15.7a.25.25 0 00.4-.2v-3.25a.25.25 0 "
        "00-.25-.25h-3.5a.25.25 0 00-.25.25z"
    ),
    "fork": (
        "M5 3.25a.75.75 0 11-1.5 0 .75.75 0 011.5 0zm0 2.122a2.25 2.25 0 10-1.5 0v.878A2.25 "
        "2.25 0 005.75 8.5h1.5v2.128a2.251 2.251 0 101.5 0V8.5h1.5a2.25 2.25 0 002.25-2.25v-.878a2.25 "
        "2.25 0 10-1.5 0v.878a.75.75 0 01-.75.75h-4.5A.75.75 0 015 6.25v-.878zm3.75 7.378a.75.75 "
        "0 11-1.5 0 .75.75 0 011.5 0zm3-8.75a.75.75 0 100-1.5.75.75 0 000 1.5z"
    ),
}

STATS_GRAPHQL_QUERY = """
query($login: String!) {
  user(login: $login) {
    login
    pullRequests(first: 1) { totalCount }
    openIssues: issues(states: OPEN) { totalCount }
    closedIssues: issues(states: CLOSED) { totalCount }
    followers { totalCount }
    repositoriesContributedTo(
      first: 1
      contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, REPOSITORY]
    ) { totalCount }
  }
}
""".strip()

RANK_CIRCUMFERENCE = 2.0 * math.pi * 40.0
STATS_CARD_WIDTH = 419
STATS_CARD_HEIGHT = 195
STATS_LINE_HEIGHT = 25
REPO_CARD_WIDTH = 400
REPO_CARD_HEIGHT = 120


@dataclass(frozen=True, slots=True)
class RepositorySpec:
    owner: str
    name: str
    asset_stem: str


REPOSITORIES = (
    RepositorySpec(
        "xzAscC", "RobustDiM-PrefixSteering", "pin-robustdim-prefixsteering"
    ),
    RepositorySpec("xzAscC", "ProbingReflection", "pin-probingreflection"),
    RepositorySpec("xzAscC", "PostDyn", "pin-postdyn"),
    RepositorySpec("GoXzascc", "AbsTopK-SAE", "pin-goxzascc-abstopk-sae"),
    RepositorySpec("xzAscC", "LLMUsage", "pin-llmusage"),
    RepositorySpec("xzAscC", "dotfiles", "pin-dotfiles"),
)


ASSET_FILENAMES = (
    "pin-robustdim-prefixsteering-light.svg",
    "pin-robustdim-prefixsteering-dark.svg",
    "pin-probingreflection-light.svg",
    "pin-probingreflection-dark.svg",
    "pin-postdyn-light.svg",
    "pin-postdyn-dark.svg",
    "pin-goxzascc-abstopk-sae-light.svg",
    "pin-goxzascc-abstopk-sae-dark.svg",
    "pin-llmusage-light.svg",
    "pin-llmusage-dark.svg",
    "pin-dotfiles-light.svg",
    "pin-dotfiles-dark.svg",
    "stats-light.svg",
    "stats-dark.svg",
    "badge-visits.svg",
    "badge-years.svg",
    "badge-repos.svg",
    "badge-commits-monthly.svg",
    "languages-light.svg",
    "languages-dark.svg",
)


@dataclass(frozen=True, slots=True)
class RepositoryStats:
    full_name: str
    description: str
    stars: int
    forks: int
    language: str | None


@dataclass(frozen=True, slots=True)
class Rank:
    level: str
    percentile: float


@dataclass(frozen=True, slots=True)
class ContributionStats:
    total_prs: int
    total_issues: int
    contributed_to: int
    followers: int


@dataclass(frozen=True, slots=True)
class AccountStats:
    username: str
    total_stars: int
    total_commits: int
    total_prs: int
    total_issues: int
    contributed_to: int
    rank_level: str
    rank_percentile: float


@dataclass(frozen=True, slots=True)
class MetricBadge:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class OwnedRepository:
    languages_url: str
    stars: int
    fork: bool


@dataclass(frozen=True, slots=True)
class AccountData:
    username: str
    public_repositories: int
    followers: int
    created_at: datetime


class GenerationError(RuntimeError):
    pass


class Fetcher(Protocol):
    def __call__(
        self, url: str, headers: Mapping[str, str], body: bytes | None = None
    ) -> bytes: ...


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "xzAscC-profile-static-assets",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def public_headers() -> dict[str, str]:
    return {
        "Accept": "image/svg+xml",
        "User-Agent": "xzAscC-profile-static-assets",
    }


def network_fetch(
    url: str, headers: Mapping[str, str], body: bytes | None = None
) -> bytes:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise GenerationError(f"Refusing non-HTTPS or credentialed URL: {url}")
    try:
        port = parsed.port
    except ValueError as error:
        raise GenerationError(f"URL contains an invalid port: {url}") from error
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    method = "POST" if body is not None else "GET"
    connection = HTTPSConnection(parsed.hostname, port=port or 443, timeout=30)
    try:
        connection.request(method, target, body=body, headers=dict(headers))
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise GenerationError(
                f"Failed to fetch {url}: HTTP {response.status} {response.reason}"
            )
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPException, OSError, TimeoutError) as error:
        raise GenerationError(f"Failed to fetch {url}: {error}") from error
    finally:
        connection.close()
    if len(payload) > MAX_RESPONSE_BYTES:
        raise GenerationError(f"Response from {url} exceeds {MAX_RESPONSE_BYTES} bytes")
    return payload


def _json_object(payload: bytes, context: str) -> object:
    try:
        text = payload.decode("utf-8")
        value = cast(object, json.loads(text))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GenerationError(f"{context} returned invalid JSON: {error}") from error
    return value


def _mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise GenerationError(f"{context} must be a JSON object")
    raw_mapping = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for key, item in raw_mapping.items():
        if not isinstance(key, str):
            raise GenerationError(f"{context} contains a non-string key")
        result[key] = item
    return result


def _list(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise GenerationError(f"{context} must be a JSON array")
    return cast(list[object], value)


def _field(data: Mapping[str, object], name: str, context: str) -> object:
    if name not in data:
        raise GenerationError(f"{context}.{name} is missing")
    return data[name]


def _string(data: Mapping[str, object], name: str, context: str) -> str:
    value = _field(data, name, context)
    if not isinstance(value, str):
        raise GenerationError(f"{context}.{name} must be a string")
    return value


def _optional_string(data: Mapping[str, object], name: str, context: str) -> str | None:
    value = _field(data, name, context)
    if value is None:
        return None
    if not isinstance(value, str):
        raise GenerationError(f"{context}.{name} must be a string or null")
    return value


def _integer(data: Mapping[str, object], name: str, context: str) -> int:
    value = _field(data, name, context)
    if type(value) is not int or value < 0:
        raise GenerationError(f"{context}.{name} must be a non-negative integer")
    return value


def _boolean(data: Mapping[str, object], name: str, context: str) -> bool:
    value = _field(data, name, context)
    if not isinstance(value, bool):
        raise GenerationError(f"{context}.{name} must be a boolean")
    return value


def _fetch_json(fetcher: Fetcher, url: str, headers: Mapping[str, str]) -> object:
    return _json_object(_fetch_bytes(fetcher, url, headers), url)


def _fetch_bytes(
    fetcher: Fetcher,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None = None,
) -> bytes:
    try:
        payload = fetcher(url, headers, body)
    except GenerationError:
        raise
    except (OSError, TimeoutError) as error:
        raise GenerationError(f"Failed to fetch {url}: {error}") from error
    if len(payload) > MAX_RESPONSE_BYTES:
        raise GenerationError(f"Response from {url} exceeds {MAX_RESPONSE_BYTES} bytes")
    return payload


def _validated_languages_url(value: str, context: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise GenerationError(f"{context}.languages_url has an invalid port") from error
    path_parts = parsed.path.split("/")
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.github.com"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or len(path_parts) != 5
        or path_parts[1] != "repos"
        or not path_parts[2]
        or not path_parts[3]
        or path_parts[4] != "languages"
    ):
        raise GenerationError(
            f"{context}.languages_url must be a GitHub API repository languages URL"
        )
    return value


def fetch_owned_repositories(
    fetcher: Fetcher, username: str, headers: Mapping[str, str]
) -> tuple[OwnedRepository, ...]:
    repositories: list[OwnedRepository] = []
    encoded_username = quote(username, safe="")
    for page in range(1, 12):
        url = (
            f"{GITHUB_API}/users/{encoded_username}/repos"
            f"?type=owner&per_page=100&page={page}"
        )
        batch = _list(_fetch_json(fetcher, url, headers), url)
        if page == 11:
            if batch:
                raise GenerationError(
                    "GitHub repository pagination exceeded 1,000 repositories"
                )
            return tuple(repositories)
        for index, item in enumerate(batch):
            context = f"{url}[{index}]"
            data = _mapping(item, context)
            repositories.append(
                OwnedRepository(
                    languages_url=_validated_languages_url(
                        _string(data, "languages_url", context), context
                    ),
                    stars=_integer(data, "stargazers_count", context),
                    fork=_boolean(data, "fork", context),
                )
            )
        if len(batch) < 100:
            return tuple(repositories)
    raise GenerationError("GitHub repository pagination ended unexpectedly")


def fetch_repository(
    fetcher: Fetcher,
    spec: RepositorySpec,
    headers: Mapping[str, str],
) -> RepositoryStats:
    url = f"{GITHUB_API}/repos/{quote(spec.owner, safe='')}/{quote(spec.name, safe='')}"
    data = _mapping(_fetch_json(fetcher, url, headers), url)
    full_name = _string(data, "full_name", url)
    if full_name.casefold() != f"{spec.owner}/{spec.name}".casefold():
        raise GenerationError(
            f"{url}.full_name does not match the requested repository"
        )
    description = _optional_string(data, "description", url)
    return RepositoryStats(
        full_name=full_name,
        description=description or "No description provided.",
        stars=_integer(data, "stargazers_count", url),
        forks=_integer(data, "forks_count", url),
        language=_optional_string(data, "language", url),
    )


def fetch_account(
    fetcher: Fetcher, username: str, headers: Mapping[str, str]
) -> AccountData:
    url = f"{GITHUB_API}/users/{quote(username, safe='')}"
    data = _mapping(_fetch_json(fetcher, url, headers), url)
    login = _string(data, "login", url)
    if login.casefold() != username.casefold():
        raise GenerationError(f"{url}.login does not match the requested account")
    created_text = _string(data, "created_at", url)
    try:
        created_at = datetime.fromisoformat(created_text.replace("Z", "+00:00"))
    except ValueError as error:
        raise GenerationError(
            f"{url}.created_at is not an ISO-8601 timestamp"
        ) from error
    if created_at.tzinfo is None:
        raise GenerationError(f"{url}.created_at must include a timezone")
    return AccountData(
        username=login,
        public_repositories=_integer(data, "public_repos", url),
        followers=_integer(data, "followers", url),
        created_at=created_at,
    )


def _search_total_count(
    fetcher: Fetcher,
    *,
    resource: str,
    query: str,
    headers: Mapping[str, str],
) -> int:
    url = f"{GITHUB_API}/search/{resource}?{urlencode({'q': query, 'per_page': 1})}"
    data = _mapping(_fetch_json(fetcher, url, headers), url)
    if _boolean(data, "incomplete_results", url):
        raise GenerationError(f"{url} returned incomplete results")
    items = _list(_field(data, "items", url), f"{url}.items")
    for index, item in enumerate(items):
        _ = _mapping(item, f"{url}.items[{index}]")
    return _integer(data, "total_count", url)


def fetch_monthly_commits(
    fetcher: Fetcher,
    username: str,
    now: datetime,
    headers: Mapping[str, str],
) -> int:
    if now.tzinfo is None:
        raise GenerationError("The generation time must include a timezone")
    last_day = calendar.monthrange(now.year, now.month)[1]
    date_range = f"{now.year:04d}-{now.month:02d}-01..{now.year:04d}-{now.month:02d}-{last_day:02d}"
    return _search_total_count(
        fetcher,
        resource="commits",
        query=f"author:{username} committer-date:{date_range}",
        headers=headers,
    )


def fetch_total_commits(
    fetcher: Fetcher,
    username: str,
    headers: Mapping[str, str],
) -> int:
    return _search_total_count(
        fetcher,
        resource="commits",
        query=f"author:{username}",
        headers=headers,
    )


def fetch_contribution_stats(
    fetcher: Fetcher,
    username: str,
    headers: Mapping[str, str],
) -> ContributionStats:
    payload = json.dumps(
        {"query": STATS_GRAPHQL_QUERY, "variables": {"login": username}},
        separators=(",", ":"),
    ).encode("utf-8")
    request_headers = {
        **dict(headers),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    data = _mapping(
        _json_object(
            _fetch_bytes(fetcher, GITHUB_GRAPHQL, request_headers, payload),
            GITHUB_GRAPHQL,
        ),
        GITHUB_GRAPHQL,
    )
    if "errors" in data and data["errors"] is not None:
        raise GenerationError(f"{GITHUB_GRAPHQL} returned GraphQL errors")
    root = _mapping(_field(data, "data", GITHUB_GRAPHQL), f"{GITHUB_GRAPHQL}.data")
    user = _mapping(
        _field(root, "user", f"{GITHUB_GRAPHQL}.data"), f"{GITHUB_GRAPHQL}.data.user"
    )
    login = _string(user, "login", f"{GITHUB_GRAPHQL}.data.user")
    if login.casefold() != username.casefold():
        raise GenerationError(f"{GITHUB_GRAPHQL}.data.user.login does not match")
    pull_requests = _mapping(
        _field(user, "pullRequests", f"{GITHUB_GRAPHQL}.data.user"),
        f"{GITHUB_GRAPHQL}.data.user.pullRequests",
    )
    open_issues = _mapping(
        _field(user, "openIssues", f"{GITHUB_GRAPHQL}.data.user"),
        f"{GITHUB_GRAPHQL}.data.user.openIssues",
    )
    closed_issues = _mapping(
        _field(user, "closedIssues", f"{GITHUB_GRAPHQL}.data.user"),
        f"{GITHUB_GRAPHQL}.data.user.closedIssues",
    )
    followers = _mapping(
        _field(user, "followers", f"{GITHUB_GRAPHQL}.data.user"),
        f"{GITHUB_GRAPHQL}.data.user.followers",
    )
    contributed = _mapping(
        _field(user, "repositoriesContributedTo", f"{GITHUB_GRAPHQL}.data.user"),
        f"{GITHUB_GRAPHQL}.data.user.repositoriesContributedTo",
    )
    return ContributionStats(
        total_prs=_integer(
            pull_requests, "totalCount", f"{GITHUB_GRAPHQL}.data.user.pullRequests"
        ),
        total_issues=_integer(
            open_issues, "totalCount", f"{GITHUB_GRAPHQL}.data.user.openIssues"
        )
        + _integer(
            closed_issues, "totalCount", f"{GITHUB_GRAPHQL}.data.user.closedIssues"
        ),
        contributed_to=_integer(
            contributed,
            "totalCount",
            f"{GITHUB_GRAPHQL}.data.user.repositoriesContributedTo",
        ),
        followers=_integer(
            followers, "totalCount", f"{GITHUB_GRAPHQL}.data.user.followers"
        ),
    )


def format_stat_number(value: int) -> str:
    if value < 0:
        raise GenerationError("Stat values must be non-negative")
    if value < 1000:
        return str(value)
    scaled = value / 1000
    text = f"{scaled:.1f}".rstrip("0").rstrip(".")
    return f"{text}k"


def calculate_rank(
    *,
    all_commits: bool,
    commits: int,
    prs: int,
    issues: int,
    reviews: int,
    stars: int,
    followers: int,
) -> Rank:
    commits_median = 1000.0 if all_commits else 250.0
    commits_weight = 2.0
    prs_median = 50.0
    prs_weight = 3.0
    issues_median = 25.0
    issues_weight = 1.0
    reviews_median = 2.0
    reviews_weight = 1.0
    stars_median = 50.0
    stars_weight = 4.0
    followers_median = 10.0
    followers_weight = 1.0
    total_weight = (
        commits_weight
        + prs_weight
        + issues_weight
        + reviews_weight
        + stars_weight
        + followers_weight
    )

    def exponential_cdf(value: float) -> float:
        return 1.0 - 2.0 ** (-value)

    def log_normal_cdf(value: float) -> float:
        return value / (1.0 + value)

    score = (
        commits_weight * exponential_cdf(commits / commits_median)
        + prs_weight * exponential_cdf(prs / prs_median)
        + issues_weight * exponential_cdf(issues / issues_median)
        + reviews_weight * exponential_cdf(reviews / reviews_median)
        + stars_weight * log_normal_cdf(stars / stars_median)
        + followers_weight * log_normal_cdf(followers / followers_median)
    ) / total_weight
    percentile = (1.0 - score) * 100.0
    thresholds = (1.0, 12.5, 25.0, 37.5, 50.0, 62.5, 75.0, 87.5, 100.0)
    levels = ("S", "A+", "A", "A-", "B+", "B", "B-", "C+", "C")
    for threshold, level in zip(thresholds, levels, strict=True):
        if percentile <= threshold:
            return Rank(level=level, percentile=percentile)
    return Rank(level="C", percentile=percentile)


def _octicon(name: str, *, x: float, y: float, fill: str, size: float = 16) -> str:
    path = STAT_ICONS[name]
    return (
        f'<svg x="{x:g}" y="{y:g}" width="{size:g}" height="{size:g}" '
        f'viewBox="0 0 16 16" class="icon" aria-hidden="true">'
        f'<path fill-rule="evenodd" d="{path}" fill="{fill}" />'
        "</svg>"
    )


def fetch_language_counts(
    fetcher: Fetcher,
    repositories: tuple[OwnedRepository, ...],
    headers: Mapping[str, str],
) -> list[dict[str, int]]:
    per_repository: list[dict[str, int]] = []
    for repository in repositories:
        if repository.fork:
            continue
        data = _mapping(
            _fetch_json(fetcher, repository.languages_url, headers),
            repository.languages_url,
        )
        counts: dict[str, int] = {}
        for language, value in data.items():
            if type(value) is not int or value < 0:
                raise GenerationError(
                    f"{repository.languages_url}.{language} must be a non-negative integer"
                )
            counts[language] = value
        per_repository.append(counts)
    return per_repository


def parse_visit_value(payload: bytes) -> str:
    if len(payload) > MAX_RESPONSE_BYTES:
        raise GenerationError(f"Visits badge exceeds {MAX_RESPONSE_BYTES} bytes")
    lowered = payload.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise GenerationError("Visits badge contains a prohibited XML declaration")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise GenerationError(f"Visits badge returned invalid SVG: {error}") from error
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        raise GenerationError("Visits badge does not have an SVG root")
    title = next(
        (
            element.text
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "title" and element.text
        ),
        None,
    )
    if title is None:
        aria_label = root.attrib.get("aria-label")
        title = aria_label if aria_label else None
    if title is None:
        raise GenerationError("Visits badge does not contain an accessible value")
    label, separator, value = title.partition(":")
    normalized = value.strip()
    if (
        not separator
        or label.strip().casefold() != "visits"
        or VISIT_VALUE_PATTERN.fullmatch(normalized) is None
    ):
        raise GenerationError("Visits badge contains an invalid displayed value")
    return normalized


def fetch_visits(fetcher: Fetcher, username: str) -> str:
    encoded = quote(username, safe="")
    url = (
        f"https://badges.strrl.dev/visits/{encoded}/{encoded}"
        "?style=flat-square&color=black&logo=github&v=2"
    )
    payload = _fetch_bytes(fetcher, url, public_headers())
    return parse_visit_value(payload)


def _theme(dark: bool) -> Theme:
    return DARK_THEME if dark else LIGHT_THEME


def _text(value: str) -> str:
    return escape(value, quote=False)


def _wrapped_lines(value: str, limit: int = 49) -> tuple[str, ...]:
    words = " ".join(value.split()).split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= limit or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if not lines:
        return ("No description provided.",)
    if len(lines) <= 2:
        return tuple(lines)
    second = " ".join(lines[1:])
    if len(second) > limit:
        second = f"{second[: limit - 1].rstrip()}…"
    return (lines[0], second)


def render_repository_card(repository: RepositoryStats, *, dark: bool) -> str:
    theme = _theme(dark)
    owner, separator, repo_name = repository.full_name.partition("/")
    header = (
        repository.full_name
        if separator and owner != DEFAULT_USERNAME
        else (repo_name or repository.full_name)
    )
    if len(header) > 35:
        header = f"{header[:34]}…"
    description_lines = _wrapped_lines(repository.description, limit=52)
    language = repository.language or "Unspecified"
    language_color = LANGUAGE_COLORS.get(
        repository.language or "", LANGUAGE_COLORS["Other"]
    )
    stars_text = format_stat_number(repository.stars)
    forks_text = format_stat_number(repository.forks)
    language_width = max(24.0, min(90.0, 8.0 + len(language) * 6.5))
    star_x = 25 + language_width + 18
    fork_x = star_x + 16 + max(12.0, len(stars_text) * 7.0) + 18
    accessible_description = (
        f"{repository.description}. {repository.stars} stars, {repository.forks} forks, "
        f"primary language {language}."
    )
    description_nodes = tuple(
        f'  <text x="25" y="{55 + index * 16}" class="description">{_text(line)}</text>'
        for index, line in enumerate(description_lines)
    )
    return "\n".join(
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{REPO_CARD_WIDTH}" '
            f'height="{REPO_CARD_HEIGHT}" viewBox="0 0 {REPO_CARD_WIDTH} {REPO_CARD_HEIGHT}" '
            'role="img" aria-labelledby="title desc">',
            f'  <title id="title">{_text(repository.full_name)}</title>',
            f'  <desc id="desc">{_text(accessible_description)}</desc>',
            "  <defs>",
            "    <style>",
            "      .title { font: 600 15.25px 'Segoe UI', Ubuntu, sans-serif; "
            + f"fill: {theme.title}; }}",
            "      .description { font: 400 13px 'Segoe UI', Ubuntu, sans-serif; "
            + f"fill: {theme.text}; }}",
            "      .meta { font: 400 12px 'Segoe UI', Ubuntu, sans-serif; "
            + f"fill: {theme.text}; }}",
            "    </style>",
            "  </defs>",
            f'  <rect x="0.5" y="0.5" width="{REPO_CARD_WIDTH - 1}" '
            f'height="{REPO_CARD_HEIGHT - 1}" rx="4.5" fill="{theme.background}" '
            f'stroke="{theme.border}" stroke-width="1" />',
            f"  {_octicon('contribs', x=25, y=18, fill=theme.icon, size=16)}",
            f'  <text x="50" y="32" class="title">{_text(header)}</text>',
            *description_nodes,
            f'  <circle cx="25" cy="98" r="5" fill="{language_color}" />',
            f'  <text x="38" y="102" class="meta">{_text(language)}</text>',
            f"  {_octicon('stars', x=star_x, y=90, fill=theme.icon, size=16)}",
            f'  <text x="{star_x + 20:g}" y="102" class="meta">{stars_text}</text>',
            f"  {_octicon('fork', x=fork_x, y=90, fill=theme.icon, size=16)}",
            f'  <text x="{fork_x + 20:g}" y="102" class="meta">{forks_text}</text>',
            "</svg>",
            "",
        )
    )


def render_account_card(account: AccountStats, *, dark: bool) -> str:
    theme = _theme(dark)
    display_name = f"{account.username[:1].upper()}{account.username[1:]}"
    title = f"{display_name}'s GitHub Stats"
    metrics = (
        ("stars", "Total Stars Earned", account.total_stars),
        ("commits", "Total Commits", account.total_commits),
        ("prs", "Total PRs", account.total_prs),
        ("issues", "Total Issues", account.total_issues),
        ("contribs", "Contributed to (last year)", account.contributed_to),
    )
    metric_nodes: list[str] = []
    for index, (icon_name, label, value) in enumerate(metrics):
        y = 55 + index * STATS_LINE_HEIGHT
        metric_nodes.extend(
            (
                f"  {_octicon(icon_name, x=25, y=y - 13, fill=theme.icon, size=16)}",
                f'  <text x="50" y="{y}" class="stat">{_text(label)}</text>',
                f'  <text x="280" y="{y}" class="stat value">'
                f"{format_stat_number(value)}</text>",
            )
        )
    progress = max(0.0, min(100.0, 100.0 - account.rank_percentile))
    dash_offset = ((100.0 - progress) / 100.0) * RANK_CIRCUMFERENCE
    rank_x = 350
    rank_y = STATS_CARD_HEIGHT / 2
    description = ", ".join(
        f"{label}: {format_stat_number(value)}" for _, label, value in metrics
    )
    return "\n".join(
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{STATS_CARD_WIDTH}" '
            f'height="{STATS_CARD_HEIGHT}" viewBox="0 0 {STATS_CARD_WIDTH} {STATS_CARD_HEIGHT}" '
            'role="img" aria-labelledby="title desc">',
            f'  <title id="title">{_text(title)}, Rank: {_text(account.rank_level)}</title>',
            f'  <desc id="desc">{_text(description)}.</desc>',
            "  <defs>",
            "    <style>",
            "      .heading { font: 600 18px 'Segoe UI', Ubuntu, sans-serif; "
            + f"fill: {theme.title}; }}",
            "      .stat { font: 600 14px 'Segoe UI', Ubuntu, sans-serif; "
            + f"fill: {theme.text}; }}",
            "      .value { text-anchor: end; }",
            "      .rank-text { font: 800 24px 'Segoe UI', Ubuntu, sans-serif; "
            + f"fill: {theme.text}; }}",
            "      .rank-circle-rim { "
            + f"stroke: {theme.ring}; fill: none; stroke-width: 6; opacity: 0.2; }}",
            "      .rank-circle { "
            + f"stroke: {theme.ring}; fill: none; stroke-width: 6; stroke-linecap: round; "
            "opacity: 0.8; transform-origin: center; transform: rotate(-90deg); }",
            "    </style>",
            "  </defs>",
            f'  <rect x="0.5" y="0.5" width="{STATS_CARD_WIDTH - 1}" '
            f'height="{STATS_CARD_HEIGHT - 1}" rx="4.5" fill="{theme.background}" '
            f'stroke="{theme.border}" stroke-width="1" />',
            f'  <text x="25" y="32" class="heading">{_text(title)}</text>',
            *metric_nodes,
            f'  <g data-testid="rank-circle" transform="translate({rank_x:g}, {rank_y:g})">',
            '    <circle class="rank-circle-rim" cx="0" cy="0" r="40" />',
            f'    <circle class="rank-circle" cx="0" cy="0" r="40" '
            f'stroke-dasharray="{RANK_CIRCUMFERENCE:.4f}" '
            f'stroke-dashoffset="{dash_offset:.4f}" />',
            f'    <text class="rank-text" x="0" y="1" text-anchor="middle" '
            f'dominant-baseline="central">{_text(account.rank_level)}</text>',
            "  </g>",
            "</svg>",
            "",
        )
    )


def render_badge(badge: MetricBadge) -> str:
    label_width = 26 + len(badge.label) * 7
    value_width = 14 + len(badge.value) * 7
    width = label_width + value_width
    accessible = f"{badge.label}: {badge.value}"
    value_center = label_width + value_width / 2
    return "\n".join(
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="20" '
            + f'viewBox="0 0 {width} 20" role="img" aria-label="{escape(accessible)}">',
            f"  <title>{_text(accessible)}</title>",
            f"  <desc>{_text(accessible)}.</desc>",
            '  <linearGradient id="surface" x2="0" y2="100%">',
            '    <stop offset="0" stop-color="#334155" />',
            '    <stop offset="1" stop-color="#0d1117" />',
            "  </linearGradient>",
            f'  <rect width="{label_width}" height="20" fill="url(#surface)" />',
            f'  <rect x="{label_width}" width="{value_width}" height="20" fill="{LIGHT_ACCENT}" />',
            '  <path fill="#ffffff" d="M10 4.2a5.8 5.8 0 0 0-1.8 11.3c.3.1.4-.1.4-.3v-1.1c-1.7.4-2.1-.7-2.1-.7-.3-.7-.7-.9-.7-.9-.6-.4 0-.4 0-.4.6 0 1 .7 1 .7.6 1 1.5.7 1.9.5.1-.4.2-.7.4-.8-1.4-.2-2.8-.7-2.8-2.9 0-.6.2-1.2.6-1.6-.1-.2-.3-.8.1-1.6 0 0 .5-.2 1.6.6a5.5 5.5 0 0 1 2.9 0c1.1-.8 1.6-.6 1.6-.6.4.8.2 1.4.1 1.6.4.4.6 1 .6 1.6 0 2.2-1.4 2.7-2.8 2.9.2.2.4.6.4 1.1v1.6c0 .2.1.4.4.3A5.8 5.8 0 0 0 10 4.2Z" />',
            '  <g fill="#ffffff" font-family="\'Segoe UI\',Ubuntu,sans-serif" font-size="11">',
            f'    <text x="21" y="14">{_text(badge.label)}</text>',
            f'    <text x="{value_center:.1f}" y="14" text-anchor="middle" font-weight="600">{_text(badge.value)}</text>',
            "  </g>",
            "</svg>",
            "",
        )
    )


def account_years(created_at: datetime, now: datetime) -> int:
    created = created_at.astimezone(timezone.utc)
    current = now.astimezone(timezone.utc)
    years = current.year - created.year
    if (current.month, current.day) < (created.month, created.day):
        years -= 1
    if years < 0:
        raise GenerationError("GitHub account creation time is in the future")
    return years


def validate_svg(filename: str, content: str) -> None:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise GenerationError(
            f"Generated {filename} is invalid XML: {error}"
        ) from error
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        raise GenerationError(f"Generated {filename} does not have an SVG root")
    child_names = {child.tag.rsplit("}", 1)[-1] for child in root}
    if "title" not in child_names or "desc" not in child_names:
        raise GenerationError(f"Generated {filename} is missing title or description")


def build_assets(
    fetcher: Fetcher,
    *,
    username: str,
    now: datetime,
) -> dict[str, str]:
    headers = github_headers()
    owned_repositories = fetch_owned_repositories(fetcher, username, headers)
    repositories = tuple(
        (spec, fetch_repository(fetcher, spec, headers)) for spec in REPOSITORIES
    )
    account_data = fetch_account(fetcher, username, headers)
    monthly_commits = fetch_monthly_commits(fetcher, username, now, headers)
    total_commits = fetch_total_commits(fetcher, username, headers)
    contribution = fetch_contribution_stats(fetcher, username, headers)
    visits = fetch_visits(fetcher, username)
    language_entries = displayed_languages(
        aggregate_weights(fetch_language_counts(fetcher, owned_repositories, headers))
    )
    if not language_entries:
        raise GenerationError("No public repository languages were found")
    total_stars = sum(
        repository.stars for repository in owned_repositories if not repository.fork
    )
    rank = calculate_rank(
        all_commits=True,
        commits=total_commits,
        prs=contribution.total_prs,
        issues=contribution.total_issues,
        reviews=0,
        stars=total_stars,
        followers=contribution.followers,
    )
    account = AccountStats(
        username=account_data.username,
        total_stars=total_stars,
        total_commits=total_commits,
        total_prs=contribution.total_prs,
        total_issues=contribution.total_issues,
        contributed_to=contribution.contributed_to,
        rank_level=rank.level,
        rank_percentile=rank.percentile,
    )
    badges = (
        ("badge-visits.svg", MetricBadge("visits", visits)),
        (
            "badge-years.svg",
            MetricBadge("years", str(account_years(account_data.created_at, now))),
        ),
        (
            "badge-repos.svg",
            MetricBadge("repos", str(account_data.public_repositories)),
        ),
        (
            "badge-commits-monthly.svg",
            MetricBadge("commits/month", str(monthly_commits)),
        ),
    )

    rendered: dict[str, str] = {}
    for spec, repository in repositories:
        rendered[f"{spec.asset_stem}-light.svg"] = render_repository_card(
            repository, dark=False
        )
        rendered[f"{spec.asset_stem}-dark.svg"] = render_repository_card(
            repository, dark=True
        )
    rendered["stats-light.svg"] = render_account_card(account, dark=False)
    rendered["stats-dark.svg"] = render_account_card(account, dark=True)
    for filename, badge in badges:
        rendered[filename] = render_badge(badge)
    rendered["languages-light.svg"] = render_language_card(language_entries, dark=False)
    rendered["languages-dark.svg"] = render_language_card(language_entries, dark=True)

    if set(rendered) != set(ASSET_FILENAMES):
        raise GenerationError("Generated asset inventory does not match the manifest")
    ordered = {filename: rendered[filename] for filename in ASSET_FILENAMES}
    for filename, content in ordered.items():
        validate_svg(filename, content)
    return ordered


def _replace_changed_assets(assets_dir: Path, rendered: Mapping[str, str]) -> None:
    assets_dir.mkdir(parents=True, exist_ok=True)
    changed = {
        filename: content
        for filename, content in rendered.items()
        if not (assets_dir / filename).exists()
        or (assets_dir / filename).read_text(encoding="utf-8") != content
    }
    if not changed:
        return

    previous = {
        filename: (assets_dir / filename).read_bytes()
        if (assets_dir / filename).exists()
        else None
        for filename in changed
    }
    replaced: list[str] = []
    try:
        with tempfile.TemporaryDirectory(
            prefix="profile-assets-", dir=assets_dir
        ) as temp:
            staging = Path(temp)
            for filename, content in changed.items():
                _ = (staging / filename).write_text(content, encoding="utf-8")
            for filename in changed:
                os.replace(staging / filename, assets_dir / filename)
                replaced.append(filename)
    except OSError as error:
        rollback_errors: list[str] = []
        for filename in reversed(replaced):
            destination = assets_dir / filename
            try:
                old_content = previous[filename]
                if old_content is None:
                    destination.unlink(missing_ok=True)
                else:
                    _ = destination.write_bytes(old_content)
            except OSError as rollback_error:
                rollback_errors.append(f"{filename}: {rollback_error}")
        details = (
            f"; rollback errors: {', '.join(rollback_errors)}"
            if rollback_errors
            else ""
        )
        raise GenerationError(
            f"Failed to update generated assets: {error}{details}"
        ) from error


def update_assets(
    fetcher: Fetcher,
    assets_dir: Path,
    *,
    username: str,
    now: datetime,
) -> None:
    rendered = build_assets(fetcher, username=username, now=now)
    _replace_changed_assets(assets_dir, rendered)


def main() -> None:
    try:
        update_assets(
            network_fetch,
            ASSETS,
            username=DEFAULT_USERNAME,
            now=datetime.now(timezone.utc),
        )
    except GenerationError as error:
        print(f"Asset generation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(f"Generated {len(ASSET_FILENAMES)} profile assets.")


if __name__ == "__main__":
    main()
