#!/usr/bin/env python3
from __future__ import annotations

import calendar
import json
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
    aggregate_weights,
    displayed_languages,
    render_svg as render_language_card,
)


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
DEFAULT_USERNAME = os.environ.get("GITHUB_REPOSITORY_OWNER", "xzAscC")
GITHUB_API = "https://api.github.com"
MAX_RESPONSE_BYTES = 1_000_000
LIGHT_ACCENT = "#0f766e"
LIGHT_REPOSITORY_BORDER = "#b45309"
DARK_REPOSITORY_BORDER = "#c2410c"
VISIT_VALUE_PATTERN = re.compile(r"[0-9]+(?:[.,][0-9]+)*(?:[kKmMbB])?")


@dataclass(frozen=True, slots=True)
class Theme:
    background: str
    title: str
    text: str
    icon: str
    border: str


DARK_THEME = Theme(
    background="#0d1117",
    title="#5eead4",
    text="#94a3b8",
    icon="#f59e0b",
    border="#1e3a32",
)
LIGHT_THEME = Theme(
    background="#ffffff",
    title=LIGHT_ACCENT,
    text="#334155",
    icon="#d97706",
    border="#e2e8f0",
)


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
class AccountStats:
    username: str
    total_stars: int
    commits_this_month: int
    public_repositories: int
    followers: int


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
    def __call__(self, url: str, headers: Mapping[str, str]) -> bytes: ...


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


def network_fetch(url: str, headers: Mapping[str, str]) -> bytes:
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
    connection = HTTPSConnection(parsed.hostname, port=port or 443, timeout=30)
    try:
        connection.request("GET", target, headers=dict(headers))
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


def _fetch_bytes(fetcher: Fetcher, url: str, headers: Mapping[str, str]) -> bytes:
    try:
        payload = fetcher(url, headers)
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
    query = f"author:{username} committer-date:{date_range}"
    url = f"{GITHUB_API}/search/commits?{urlencode({'q': query, 'per_page': 1})}"
    data = _mapping(_fetch_json(fetcher, url, headers), url)
    if _boolean(data, "incomplete_results", url):
        raise GenerationError(f"{url} returned incomplete results")
    items = _list(_field(data, "items", url), f"{url}.items")
    for index, item in enumerate(items):
        _ = _mapping(item, f"{url}.items[{index}]")
    return _integer(data, "total_count", url)


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
    border = DARK_REPOSITORY_BORDER if dark else LIGHT_REPOSITORY_BORDER
    description_lines = _wrapped_lines(repository.description)
    language = repository.language or "Not specified"
    accessible_description = (
        f"{repository.description}. {repository.stars} stars, {repository.forks} forks, "
        f"primary language {language}."
    )
    rendered_description = tuple(
        f'  <text x="20" y="{63 + index * 18}" class="body">{_text(line)}</text>'
        for index, line in enumerate(description_lines)
    )
    language_width = min(72, max(24, 8 + len(language) * 6))
    return "\n".join(
        (
            '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="135" '
            + 'viewBox="0 0 400 135" role="img" aria-labelledby="title desc">',
            f'  <title id="title">{_text(repository.full_name)}</title>',
            f'  <desc id="desc">{_text(accessible_description)}</desc>',
            "  <defs>",
            "    <style>",
            "      .title { font: 600 15.25px 'Segoe UI', Ubuntu, sans-serif; "
            + f"fill: {theme.title}; }}",
            "      .body { font: 400 12px 'Segoe UI', Ubuntu, sans-serif; "
            + f"fill: {theme.text}; }}",
            "      .meta { font: 400 11px 'Segoe UI', Ubuntu, sans-serif; "
            + f"fill: {theme.text}; }}",
            "      .icon { font: 400 11px 'Segoe UI', Ubuntu, sans-serif; "
            + f"fill: {theme.icon}; }}",
            "    </style>",
            "  </defs>",
            f'  <rect x="2" y="2" width="396" height="131" rx="4" '
            + f'fill="{theme.background}" stroke="{border}" stroke-width="4" />',
            f'  <text x="20" y="33" class="title">{_text(repository.full_name)}</text>',
            *rendered_description,
            f'  <circle cx="24" cy="115" r="5" fill="{theme.title}" />',
            f'  <text x="35" y="119" class="meta">{_text(language)}</text>',
            f'  <text x="{42 + language_width}" y="119" class="icon">★</text>',
            f'  <text x="{57 + language_width}" y="119" class="meta">{repository.stars}</text>',
            f'  <text x="{88 + language_width}" y="119" class="icon">⑂</text>',
            f'  <text x="{103 + language_width}" y="119" class="meta">{repository.forks}</text>',
            "</svg>",
            "",
        )
    )


def render_account_card(account: AccountStats, *, dark: bool) -> str:
    theme = _theme(dark)
    display_name = f"{account.username[:1].upper()}{account.username[1:]}"
    title = f"{display_name}'s GitHub Stats"
    metrics = (
        ("Total Stars", account.total_stars, 54),
        ("Commits This Month", account.commits_this_month, 80),
        ("Public Repositories", account.public_repositories, 106),
        ("Followers", account.followers, 132),
    )
    metric_nodes: list[str] = []
    for label, value, y in metrics:
        metric_nodes.extend(
            (
                f'  <circle cx="25" cy="{y - 4}" r="4" fill="{theme.icon}" />',
                f'  <text x="39" y="{y}" class="label">{label}:</text>',
                f'  <text x="286" y="{y}" class="value">{value}</text>',
            )
        )
    description = "; ".join(f"{label} {value}" for label, value, _ in metrics)
    return "\n".join(
        (
            '<svg xmlns="http://www.w3.org/2000/svg" width="419" height="165" '
            + 'viewBox="0 0 419 165" role="img" aria-labelledby="title desc">',
            f'  <title id="title">{_text(title)}</title>',
            f'  <desc id="desc">{_text(description)}.</desc>',
            "  <defs>",
            "    <style>",
            "      .heading { font: 600 15.25px 'Segoe UI', Ubuntu, sans-serif; "
            + f"fill: {theme.title}; }}",
            "      .label { font: 400 12px 'Segoe UI', Ubuntu, sans-serif; "
            + f"fill: {theme.text}; }}",
            "      .value { font: 600 12px 'Segoe UI', Ubuntu, sans-serif; "
            + f"fill: {theme.text}; text-anchor: end; }}",
            "    </style>",
            "  </defs>",
            f'  <rect width="419" height="165" rx="4" fill="{theme.background}" '
            + f'stroke="{theme.border}" stroke-opacity="0" />',
            f'  <text x="20" y="29" class="heading">{_text(title)}</text>',
            *metric_nodes,
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
    visits = fetch_visits(fetcher, username)
    language_entries = displayed_languages(
        aggregate_weights(fetch_language_counts(fetcher, owned_repositories, headers))
    )
    if not language_entries:
        raise GenerationError("No public repository languages were found")
    account = AccountStats(
        username=account_data.username,
        total_stars=sum(
            repository.stars for repository in owned_repositories if not repository.fork
        ),
        commits_this_month=monthly_commits,
        public_repositories=account_data.public_repositories,
        followers=account_data.followers,
    )
    badges = (
        ("badge-visits.svg", MetricBadge("visits", visits)),
        (
            "badge-years.svg",
            MetricBadge("years", str(account_years(account_data.created_at, now))),
        ),
        ("badge-repos.svg", MetricBadge("repos", str(account.public_repositories))),
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
