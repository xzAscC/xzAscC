#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from html import escape
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


USERNAME = os.environ.get("GITHUB_REPOSITORY_OWNER", "xzAscC")
ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
BAR_X = 20.0
BAR_WIDTH = 379.0

LANGUAGE_COLORS = {
    "C": "#555555",
    "C++": "#f34b7d",
    "CSS": "#563d7c",
    "Go": "#00ADD8",
    "HTML": "#e34c26",
    "Java": "#b07219",
    "JavaScript": "#f1e05a",
    "Jupyter Notebook": "#DA5B0B",
    "Lua": "#000080",
    "Python": "#3572A5",
    "Rust": "#dea584",
    "SCSS": "#c6538c",
    "Shell": "#89e051",
    "TeX": "#3D6117",
    "TypeScript": "#3178c6",
    "Other": "#64748b",
}
PREFERRED_ORDER = {
    name: index
    for index, name in enumerate(("Python", "TeX", "SCSS", "Lua", "TypeScript"))
}


def github_headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "xzAscC-profile-language-card",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_repositories(
    username: str, headers: dict[str, str]
) -> list[dict[str, object]]:
    repositories: list[dict[str, object]] = []

    for page in range(1, 11):
        url = (
            f"https://api.github.com/users/{quote(username, safe='')}/repos"
            f"?type=owner&per_page=100&page={page}"
        )
        with urlopen(Request(url, headers=headers), timeout=30) as response:
            batch = json.load(response)
        if not isinstance(batch, list):
            raise RuntimeError("GitHub returned an unexpected repositories response")
        repositories.extend(batch)
        if len(batch) < 100:
            break

    return repositories


def fetch_repository_languages(
    repository: dict[str, object], headers: dict[str, str]
) -> dict[str, int]:
    url = repository.get("languages_url")
    if not isinstance(url, str):
        return {}
    try:
        with urlopen(Request(url, headers=headers), timeout=30) as response:
            data = json.load(response)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        name: byte_count
        for name, byte_count in data.items()
        if isinstance(name, str) and isinstance(byte_count, int)
    }


def aggregate_weights(
    per_repo_language_counts: list[dict[str, int]],
) -> dict[str, float]:
    weights: dict[str, float] = {}
    for counts in per_repo_language_counts:
        total = sum(counts.values())
        if total <= 0:
            continue
        for language, byte_count in counts.items():
            weights[language] = weights.get(language, 0.0) + byte_count / total
    return weights


def language_weights(
    repositories: list[dict[str, object]], headers: dict[str, str]
) -> dict[str, float]:
    per_repo_counts: list[dict[str, int]] = []
    for repository in repositories:
        if repository.get("fork") is not False:
            continue
        per_repo_counts.append(fetch_repository_languages(repository, headers))
    return aggregate_weights(per_repo_counts)


def displayed_languages(weights: dict[str, float]) -> list[tuple[str, float]]:
    ranked = sorted(
        weights.items(),
        key=lambda item: (
            -item[1],
            PREFERRED_ORDER.get(item[0], len(PREFERRED_ORDER)),
            item[0].casefold(),
        ),
    )
    if len(ranked) <= 6:
        return ranked
    return ranked[:5] + [("Other", sum(weight for _, weight in ranked[5:]))]


def render_svg(entries: list[tuple[str, float]], *, dark: bool) -> str:
    if not entries:
        raise RuntimeError("No public repository languages were found")

    total = sum(weight for _, weight in entries)
    heading_color = "#5eead4" if dark else "#0d9488"
    label_color = "#94a3b8" if dark else "#334155"
    descriptions = "; ".join(
        f"{name} {weight / total * 100:.2f} percent" for name, weight in entries
    )

    segments: list[str] = []
    current_x = BAR_X
    for index, (name, weight) in enumerate(entries):
        width = (
            BAR_X + BAR_WIDTH - current_x
            if index == len(entries) - 1
            else BAR_WIDTH * weight / total
        )
        segments.append(
            f'    <rect x="{current_x:.2f}" y="48" width="{width:.2f}" '
            f'height="8" fill="{LANGUAGE_COLORS.get(name, "#64748b")}" />'
        )
        current_x += width

    legends: list[str] = []
    for index, (name, weight) in enumerate(entries):
        column = index % 2
        row = index // 2
        circle_x = 25 + column * 200
        text_x = 39 + column * 200
        circle_y = 82 + row * 26
        text_y = 86 + row * 26
        label = name if len(name) <= 16 else f"{name[:15]}…"
        color = LANGUAGE_COLORS.get(name, "#64748b")
        legends.extend(
            (
                f'    <circle cx="{circle_x}" cy="{circle_y}" r="5" fill="{color}" />',
                f'    <text x="{text_x}" y="{text_y}">'
                f"{escape(label)} {weight / total * 100:.2f}%</text>",
            )
        )

    return "\n".join(
        (
            '<svg xmlns="http://www.w3.org/2000/svg" width="419" height="165" '
            'viewBox="0 0 419 165" role="img" aria-labelledby="title desc">',
            '  <title id="title">Top Languages by Repository</title>',
            f'  <desc id="desc">{escape(descriptions)}.</desc>',
            "  <defs>",
            '    <clipPath id="bar-clip">',
            '      <rect x="20" y="48" width="379" height="8" rx="4" />',
            "    </clipPath>",
            "    <style>",
            "      .heading { font: 600 15.25px 'Segoe UI', Ubuntu, sans-serif; "
            f"fill: {heading_color}; }}",
            "      .label { font: 400 12px 'Segoe UI', Ubuntu, sans-serif; "
            f"fill: {label_color}; }}",
            "    </style>",
            "  </defs>",
            "",
            '  <text x="20" y="29" class="heading">Top Languages by Repository</text>',
            "",
            '  <g clip-path="url(#bar-clip)">',
            *segments,
            "  </g>",
            "",
            '  <g class="label">',
            *legends,
            "  </g>",
            "</svg>",
            "",
        )
    )


def write_if_changed(path: Path, content: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def main() -> None:
    headers = github_headers()
    entries = displayed_languages(
        language_weights(fetch_repositories(USERNAME, headers), headers)
    )
    ASSETS.mkdir(exist_ok=True)
    write_if_changed(ASSETS / "languages-light.svg", render_svg(entries, dark=False))
    write_if_changed(ASSETS / "languages-dark.svg", render_svg(entries, dark=True))
    print(", ".join(f"{name}: {weight:.3f}" for name, weight in entries))


if __name__ == "__main__":
    main()
