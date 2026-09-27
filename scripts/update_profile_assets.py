#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
GITHUB_API = "https://api.github.com"
MAX_RESPONSE_BYTES = 1_000_000
USER_AGENT = "xzAscC-profile-static-assets"
LIGHT_ACCENT = "#404b91"
LANGUAGE_COLORS = {
    "Lua": "#000080",
    "Python": "#3572A5",
    "TeX": "#3D6117",
    "TypeScript": "#3178c6",
    "Other": "#64748b",
}


@dataclass(frozen=True, slots=True)
class Theme:
    background: str
    title: str
    text: str
    icon: str
    border: str
    chip: str
    chip_text: str


DARK_THEME = Theme(
    background="#0d1117",
    title="#aeb8ff",
    text="#bdb2a7",
    icon="#e18a6e",
    border="#30363d",
    chip="#34395c",
    chip_text="#c8ceff",
)
LIGHT_THEME = Theme(
    background="#ffffff",
    title=LIGHT_ACCENT,
    text="#6f655d",
    icon="#b65f45",
    border="#d8d0c4",
    chip="#e4e5f1",
    chip_text=LIGHT_ACCENT,
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

REPO_CARD_WIDTH = 400
REPO_CARD_HEIGHT = 120
TITLE_CHAR_WIDTH = 8.6
VENUE_CHAR_WIDTH = 6.4
VENUE_PADDING = 9


@dataclass(frozen=True, slots=True)
class RepositorySpec:
    owner: str
    name: str
    asset_stem: str
    description: str | None = None
    venue: str | None = None


REPOSITORIES = (
    RepositorySpec(
        "xzAscC",
        "RobustDiM-PrefixSteering",
        "pin-robustdim-prefixsteering",
        "Not All Tokens Are Equally Useful for Steering: Robust Directions "
        "and Prefix Steering",
        "Under review",
    ),
    RepositorySpec(
        "xzAscC",
        "ProbingReflection",
        "pin-probingreflection",
        "From Emergence to Control: Probing and Modulating Self-Reflection "
        "in Language Models",
        "TMLR 2026",
    ),
    RepositorySpec(
        "xzAscC",
        "PostDyn",
        "pin-postdyn",
        "Post-training dynamics of SFT/RL: checkpoint trajectories and "
        "concept directions.",
        "Ongoing",
    ),
    RepositorySpec(
        "GoXzascc",
        "AbsTopK-SAE",
        "pin-goxzascc-abstopk-sae",
        "AbsTopK: Rethinking Sparse Autoencoders For Bidirectional Features",
        "ICLR 2026",
    ),
    RepositorySpec(
        "xzAscC",
        "LLMUsage",
        "pin-llmusage",
        "Local-first LLM subscription usage in your Hyprland bar: "
        "OpenAI, GLM, Grok, Claude.",
    ),
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
)


@dataclass(frozen=True, slots=True)
class RepositoryStats:
    full_name: str
    description: str
    stars: int
    forks: int
    language: str | None
    venue: str | None = None


class GenerationError(RuntimeError):
    pass


class Fetcher(Protocol):
    def __call__(
        self, url: str, headers: Mapping[str, str], body: bytes | None = None
    ) -> bytes: ...


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2026-03-10",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


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
    description = spec.description or _optional_string(data, "description", url)
    return RepositoryStats(
        full_name=full_name,
        description=description or "No description provided.",
        stars=_integer(data, "stargazers_count", url),
        forks=_integer(data, "forks_count", url),
        language=_optional_string(data, "language", url),
        venue=spec.venue,
    )


def format_stat_number(value: int) -> str:
    if value < 0:
        raise GenerationError("Stat values must be non-negative")
    if value < 1000:
        return str(value)
    scaled = value / 1000
    text = f"{scaled:.1f}".rstrip("0").rstrip(".")
    return f"{text}k"


def _octicon(name: str, *, x: float, y: float, fill: str, size: float = 16) -> str:
    path = STAT_ICONS[name]
    return (
        f'<svg x="{x:g}" y="{y:g}" width="{size:g}" height="{size:g}" '
        f'viewBox="0 0 16 16" class="icon" aria-hidden="true">'
        f'<path fill-rule="evenodd" d="{path}" fill="{fill}" />'
        "</svg>"
    )


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


def _relative_luminance(color: str) -> float:
    channels = tuple(int(color[index : index + 2], 16) / 255 for index in (1, 3, 5))
    linear = tuple(
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    )
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def _visible_dot_color(color: str, background: str) -> str:
    """Lighten a language color until it stands out from the card background."""
    if _contrast(color, background) >= 3.0:
        return color
    channels = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
    for step in range(1, 11):
        mix = step / 10
        candidate = "#" + "".join(
            f"{round(channel + (255 - channel) * mix):02x}" for channel in channels
        )
        if _contrast(candidate, background) >= 3.0:
            return candidate
    return "#ffffff"


def _venue_pill(venue: str | None, theme: Theme) -> tuple[float, tuple[str, ...]]:
    if venue is None:
        return 0.0, ()
    width = round(len(venue) * VENUE_CHAR_WIDTH + 2 * VENUE_PADDING)
    right = REPO_CARD_WIDTH - 25
    return width, (
        f'  <rect x="{right - width}" y="18" width="{width}" height="19" rx="9.5" '
        f'fill="{theme.chip}" />',
        f'  <text x="{right - VENUE_PADDING}" y="31.5" text-anchor="end" '
        f'class="venue">{_text(venue)}</text>',
    )


def render_repository_card(repository: RepositoryStats, *, dark: bool) -> str:
    theme = _theme(dark)
    header = repository.full_name.rpartition("/")[2] or repository.full_name
    pill_width, pill_nodes = _venue_pill(repository.venue, theme)
    title_room = REPO_CARD_WIDTH - 25 - 50 - (pill_width + 10 if pill_width else 0)
    max_title_chars = int(title_room // TITLE_CHAR_WIDTH)
    if len(header) > max_title_chars:
        header = f"{header[: max_title_chars - 1]}…"
    description_lines = _wrapped_lines(repository.description, limit=52)
    language = repository.language or "Unspecified"
    language_color = _visible_dot_color(
        LANGUAGE_COLORS.get(repository.language or "", LANGUAGE_COLORS["Other"]),
        theme.background,
    )
    stars_text = format_stat_number(repository.stars)
    forks_text = format_stat_number(repository.forks)
    language_width = max(24.0, min(90.0, 8.0 + len(language) * 6.5))
    star_x = 25 + language_width + 18
    fork_x = star_x + 16 + max(12.0, len(stars_text) * 7.0) + 18
    venue_note = f" ({repository.venue})" if repository.venue else ""
    accessible_description = (
        f"{repository.description}{venue_note}. {repository.stars} stars, {repository.forks} forks, "
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
            "      .venue { font: 600 11px 'Segoe UI', Ubuntu, sans-serif; "
            + f"fill: {theme.chip_text}; }}",
            "    </style>",
            "  </defs>",
            f'  <rect x="0.5" y="0.5" width="{REPO_CARD_WIDTH - 1}" '
            f'height="{REPO_CARD_HEIGHT - 1}" rx="4.5" fill="{theme.background}" '
            f'stroke="{theme.border}" stroke-width="1" />',
            f"  {_octicon('contribs', x=25, y=18, fill=theme.icon, size=16)}",
            f'  <text x="50" y="32" class="title">{_text(header)}</text>',
            *pill_nodes,
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


def build_assets(fetcher: Fetcher) -> dict[str, str]:
    headers = github_headers()
    rendered: dict[str, str] = {}
    for spec in REPOSITORIES:
        repository = fetch_repository(fetcher, spec, headers)
        rendered[f"{spec.asset_stem}-light.svg"] = render_repository_card(
            repository, dark=False
        )
        rendered[f"{spec.asset_stem}-dark.svg"] = render_repository_card(
            repository, dark=True
        )

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


def update_assets(fetcher: Fetcher, assets_dir: Path) -> None:
    rendered = build_assets(fetcher)
    _replace_changed_assets(assets_dir, rendered)


def main() -> None:
    try:
        update_assets(network_fetch, ASSETS)
    except GenerationError as error:
        print(f"Asset generation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(f"Generated {len(ASSET_FILENAMES)} profile assets.")


if __name__ == "__main__":
    main()
