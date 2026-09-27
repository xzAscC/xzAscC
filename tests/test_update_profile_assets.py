"""Deterministic tests for the complete profile asset generator."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote
from unittest.mock import patch

from scripts.update_profile_assets import (
    ASSET_FILENAMES,
    LIGHT_THEME,
    AccountStats,
    GenerationError,
    RepositoryStats,
    REPOSITORIES,
    build_assets,
    fetch_monthly_commits,
    fetch_owned_repositories,
    format_stat_number,
    render_account_card,
    render_repository_card,
    update_assets,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
SVG_NAMESPACE = "{http://www.w3.org/2000/svg}"
AVATAR_JPEG = b"\xff\xd8\xff\xe0fake-jpeg-avatar"
AVATAR_DATA_URI = "data:image/jpeg;base64,/9j/4GZha2UtanBlZy1hdmF0YXI="


def css_fill(svg: str, class_name: str) -> str:
    match = re.search(
        rf"\.{re.escape(class_name)}\s*\{{[^}}]*fill:\s*(#[0-9a-fA-F]{{6}})",
        svg,
    )
    if match is None:
        raise AssertionError(f"Missing CSS fill for {class_name}")
    return match.group(1)


def relative_luminance(color: str) -> float:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise AssertionError(f"Invalid color: {color}")
    channels = tuple(int(color[index : index + 2], 16) / 255 for index in (1, 3, 5))
    linear = tuple(
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    )
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground: str, background: str) -> float:
    foreground_luminance = relative_luminance(foreground)
    background_luminance = relative_luminance(background)
    lighter = max(foreground_luminance, background_luminance)
    darker = min(foreground_luminance, background_luminance)
    return (lighter + 0.05) / (darker + 0.05)


class FakeFetcher:
    def __init__(self, responses: Mapping[str, bytes]) -> None:
        self.responses: dict[str, bytes] = dict(responses)
        self.requests: list[str] = []
        self.request_headers: list[tuple[str, dict[str, str]]] = []
        self.request_bodies: list[bytes | None] = []

    def __call__(
        self, url: str, headers: Mapping[str, str], body: bytes | None = None
    ) -> bytes:
        self.requests.append(url)
        self.request_headers.append((url, dict(headers)))
        self.request_bodies.append(body)
        if not headers.get("User-Agent"):
            raise AssertionError("Every request must identify the generator")
        try:
            return self.responses[url]
        except KeyError as error:
            raise AssertionError(f"Unexpected network request: {url}") from error


def json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def repository_payload(
    owner: str,
    name: str,
    *,
    description: str | None = None,
    stars: int = 3,
    forks: int = 2,
    language: str | None = "Python",
) -> dict[str, object]:
    return {
        "name": name,
        "full_name": f"{owner}/{name}",
        "description": description or f"Description for {name}",
        "stargazers_count": stars,
        "forks_count": forks,
        "language": language,
        "fork": False,
        "languages_url": f"https://api.github.com/repos/{owner}/{name}/languages",
    }


def complete_responses() -> dict[str, bytes]:
    repositories = [
        repository_payload("xzAscC", "RobustDiM-PrefixSteering", stars=11),
        repository_payload("xzAscC", "ProbingReflection", stars=7),
        repository_payload("xzAscC", "PostDyn", stars=5),
        repository_payload("xzAscC", "LLMUsage", stars=13),
        repository_payload("xzAscC", "dotfiles", stars=17),
    ]
    responses = {
        "https://api.github.com/users/xzAscC/repos?type=owner&per_page=100&page=1": json_bytes(
            repositories
        ),
        "https://api.github.com/users/xzAscC": json_bytes(
            {
                "login": "xzAscC",
                "name": "xzAscC",
                "public_repos": 31,
                "followers": 42,
                "created_at": "2018-09-01T00:00:00Z",
                "avatar_url": "https://avatars.githubusercontent.com/u/91479366?v=4",
            }
        ),
        "https://avatars.githubusercontent.com/u/91479366?v=4&s=160": AVATAR_JPEG,
        "https://api.github.com/search/commits?q=author%3AxzAscC+committer-date%3A2026-08-01..2026-08-31&per_page=1": json_bytes(
            {"total_count": 19, "incomplete_results": False, "items": []}
        ),
        "https://api.github.com/search/commits?q=author%3AxzAscC&per_page=1": json_bytes(
            {"total_count": 10700, "incomplete_results": False, "items": []}
        ),
        "https://api.github.com/graphql": json_bytes(
            {
                "data": {
                    "user": {
                        "login": "xzAscC",
                        "pullRequests": {"totalCount": 1700},
                        "openIssues": {"totalCount": 8},
                        "closedIssues": {"totalCount": 14},
                    }
                }
            }
        ),
    }
    pin_specs = (
        ("xzAscC", "RobustDiM-PrefixSteering"),
        ("xzAscC", "ProbingReflection"),
        ("xzAscC", "PostDyn"),
        ("GoXzascc", "AbsTopK-SAE"),
        ("xzAscC", "LLMUsage"),
        ("xzAscC", "dotfiles"),
    )
    for owner, name in pin_specs:
        payload = repository_payload(
            owner,
            name,
            description="Research <tools> & reliable model analysis",
            stars=23,
            forks=4,
            language="Python" if name != "dotfiles" else "Lua",
        )
        responses[f"https://api.github.com/repos/{owner}/{name}"] = json_bytes(payload)
    return responses


class TestRenderers(unittest.TestCase):
    def test_repository_card_matches_github_stats_extended_pin_layout(self) -> None:
        repository = RepositoryStats(
            full_name="xzAscC/example",
            description="A deterministic repository card",
            stars=12,
            forks=3,
            language="Python",
        )

        for dark, expected_border in ((False, "#d8d0c4"), (True, "#30363d")):
            with self.subTest(dark=dark):
                svg = render_repository_card(repository, dark=dark)
                root = ET.fromstring(svg)
                card = root.find(f"{SVG_NAMESPACE}rect")
                self.assertIsNotNone(card)
                if card is None:
                    raise AssertionError("Repository card is missing its background")
                self.assertEqual(card.attrib["stroke"], expected_border)
                self.assertEqual(card.attrib["stroke-width"], "1")
                self.assertEqual(card.attrib["width"], "399")
                self.assertEqual(card.attrib["height"], "119")

                circles = list(root.findall(f"{SVG_NAMESPACE}circle"))
                self.assertEqual(len(circles), 1)
                self.assertEqual(circles[0].attrib["fill"], "#3572A5")

                texts = list(root.findall(f"{SVG_NAMESPACE}text"))
                title = next(
                    node for node in texts if node.attrib.get("class") == "title"
                )
                body = next(
                    node for node in texts if node.attrib.get("class") == "description"
                )
                self.assertEqual(title.text, "example")
                self.assertEqual(body.attrib["x"], "25")
                self.assertEqual(title.attrib["x"], "50")
                self.assertGreaterEqual(svg.count('class="icon"'), 3)
                self.assertIn('viewBox="0 0 16 16"', svg)
                self.assertNotIn("★", svg)
                self.assertNotIn("⑂", svg)

    def test_repository_card_title_omits_owner_for_external_repositories(
        self,
    ) -> None:
        repository = RepositoryStats(
            full_name="GoXzascc/AbsTopK-SAE",
            description="A deterministic repository card",
            stars=1,
            forks=1,
            language="Python",
        )
        svg = render_repository_card(repository, dark=True)

        self.assertIn('class="title">AbsTopK-SAE</text>', svg)
        self.assertNotIn(">GoXzascc/AbsTopK-SAE</text>", svg)

    def test_pinned_description_overrides_fit_without_truncation(self) -> None:
        responses = complete_responses()
        for spec in REPOSITORIES:
            with self.subTest(repository=spec.name):
                svg = build_assets(
                    FakeFetcher(responses), username="xzAscC", now=NOW
                )[f"{spec.asset_stem}-dark.svg"]
                if spec.description is not None:
                    self.assertNotIn("…", svg)
                    self.assertIn(spec.description.split(" ")[0], svg)
        overridden = {spec.name for spec in REPOSITORIES if spec.description}
        self.assertEqual(
            overridden,
            {
                "RobustDiM-PrefixSteering",
                "ProbingReflection",
                "PostDyn",
                "AbsTopK-SAE",
                "LLMUsage",
            },
        )

    def test_research_cards_show_a_venue_pill(self) -> None:
        venues = {spec.name: spec.venue for spec in REPOSITORIES}
        self.assertEqual(
            venues,
            {
                "RobustDiM-PrefixSteering": "Under review",
                "ProbingReflection": "TMLR 2026",
                "PostDyn": "Ongoing",
                "AbsTopK-SAE": "ICLR 2026",
                "LLMUsage": None,
                "dotfiles": None,
            },
        )
        assets = build_assets(
            FakeFetcher(complete_responses()), username="xzAscC", now=NOW
        )
        for spec in REPOSITORIES:
            for theme in ("light", "dark"):
                with self.subTest(repository=spec.name, theme=theme):
                    svg = assets[f"{spec.asset_stem}-{theme}.svg"]
                    root = ET.fromstring(svg)
                    pills = [
                        node
                        for node in root.iter(f"{SVG_NAMESPACE}text")
                        if node.attrib.get("class") == "venue"
                    ]
                    if spec.venue is None:
                        self.assertEqual(pills, [])
                        continue
                    self.assertEqual([node.text for node in pills], [spec.venue])
                    self.assertEqual(pills[0].attrib["text-anchor"], "end")
                    self.assertNotIn(f"[{spec.venue}]", svg)

    def test_language_dot_stays_visible_on_dark_cards(self) -> None:
        repository = RepositoryStats("xzAscC/dotfiles", "Dotfiles", 1, 0, "Lua")
        for dark, background in ((True, "#0d1117"), (False, "#ffffff")):
            with self.subTest(dark=dark):
                root = ET.fromstring(render_repository_card(repository, dark=dark))
                dot = next(root.iter(f"{SVG_NAMESPACE}circle"))
                self.assertGreaterEqual(
                    contrast_ratio(dot.attrib["fill"], background), 3.0
                )

    def test_repository_card_is_accessible_valid_escaped_and_themed(self) -> None:
        repository = RepositoryStats(
            full_name="xzAscC/Research<&>",
            description="Understand <reasoning> & representations",
            stars=12,
            forks=3,
            language="Python & TeX",
        )
        dark = render_repository_card(repository, dark=True)
        light = render_repository_card(repository, dark=False)

        for svg in (dark, light):
            root = ET.fromstring(svg)
            self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
            self.assertEqual(root.attrib["width"], "400")
            self.assertEqual(root.attrib["height"], "120")
            self.assertIn("aria-labelledby", root.attrib)
            self.assertIn("Research&lt;&amp;&gt;", svg)
            self.assertIn("&lt;reasoning&gt; &amp; representations", svg)
        self.assertIn("#aeb8ff", dark)
        self.assertIn("#bdb2a7", dark)
        self.assertIn("#0d1117", dark)
        self.assertIn("#404b91", light)
        self.assertIn("#6f655d", light)
        self.assertIn("#ffffff", light)
        self.assertGreaterEqual(dark.count('class="icon"'), 3)

    def test_account_card_matches_github_stats_extended_layout(self) -> None:
        account = AccountStats(
            display_name="Xudong",
            total_stars=53,
            total_commits=10700,
            monthly_commits=19,
            total_prs=1700,
            total_issues=22,
            avatar_data_uri=AVATAR_DATA_URI,
        )
        dark = render_account_card(account, dark=True)
        light = render_account_card(account, dark=False)

        for svg in (dark, light):
            root = ET.fromstring(svg)
            self.assertEqual(root.attrib["width"], "804")
            self.assertEqual(root.attrib["height"], "120")
            self.assertIn("Xudong's GitHub Stats", svg)
            labels = [
                node.text
                for node in root.iter(f"{SVG_NAMESPACE}text")
                if node.attrib.get("class") == "label"
            ]
            self.assertEqual(
                labels,
                [
                    "Stars earned",
                    "Total commits",
                    "Commits this month",
                    "Pull requests",
                    "Issues",
                ],
            )
            label_xs = [
                float(node.attrib["x"])
                for node in root.iter(f"{SVG_NAMESPACE}text")
                if node.attrib.get("class") == "label"
            ]
            gaps = {round(b - a, 3) for a, b in zip(label_xs, label_xs[1:])}
            self.assertEqual(len(gaps), 1)
            self.assertGreaterEqual(gaps.pop(), len("Commits this month") * 6.5)
            self.assertNotIn("Contributed to", svg)
            self.assertIn("10.7k", svg)
            self.assertIn("1.7k", svg)
            self.assertIn(">53<", svg)
            self.assertIn(">22<", svg)
            self.assertNotIn("rank", svg.casefold())
            self.assertNotIn(">A<", svg)
            self.assertIn(f'href="{AVATAR_DATA_URI}"', svg)
            self.assertIn('clip-path="url(#avatar-clip)"', svg)
            self.assertIn('<clipPath id="avatar-clip">', svg)
            self.assertIn('class="icon"', svg)
            self.assertIn(">19<", svg)
            self.assertNotIn("Public Repositories", svg)
            self.assertNotIn("Followers", svg)
        self.assertIn("#aeb8ff", dark)
        self.assertIn("#404b91", light)
        self.assertNotEqual(dark, light)

    def test_format_stat_number_uses_short_k_suffix(self) -> None:
        self.assertEqual(format_stat_number(999), "999")
        self.assertEqual(format_stat_number(1000), "1k")
        self.assertEqual(format_stat_number(10700), "10.7k")
        self.assertEqual(format_stat_number(1700), "1.7k")

    def test_all_small_light_theme_text_meets_wcag_aa_contrast(self) -> None:
        repository = render_repository_card(
            RepositoryStats("xzAscC/example", "Description", 1, 1, "Python"),
            dark=False,
        )
        venue_card = render_repository_card(
            RepositoryStats("xzAscC/example", "Description", 1, 1, "Python", "ICLR 2026"),
            dark=False,
        )
        account = render_account_card(
            AccountStats("Xudong", 1, 1, 1, 1, 1, AVATAR_DATA_URI), dark=False
        )
        pairs = (
            ("repository title", css_fill(repository, "title"), LIGHT_THEME.background),
            (
                "repository description",
                css_fill(repository, "description"),
                LIGHT_THEME.background,
            ),
            (
                "repository metadata",
                css_fill(repository, "meta"),
                LIGHT_THEME.background,
            ),
            ("venue pill", css_fill(venue_card, "venue"), LIGHT_THEME.chip),
            ("account heading", css_fill(account, "heading"), LIGHT_THEME.background),
            ("account label", css_fill(account, "label"), LIGHT_THEME.background),
            ("account value", css_fill(account, "value"), LIGHT_THEME.background),
        )
        for label, foreground, background in pairs:
            with self.subTest(label=label):
                self.assertGreaterEqual(
                    contrast_ratio(foreground, background),
                    4.5,
                    f"{label}: {foreground} on {background}",
                )


class TestGeneration(unittest.TestCase):
    def test_inventory_and_generated_assets_are_complete_valid_and_deterministic(
        self,
    ) -> None:
        fetcher = FakeFetcher(complete_responses())
        first = build_assets(fetcher, username="xzAscC", now=NOW)
        second = build_assets(
            FakeFetcher(complete_responses()), username="xzAscC", now=NOW
        )

        self.assertEqual(tuple(first), ASSET_FILENAMES)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 14)
        self.assertFalse(any(name.startswith("badge-") for name in first))
        for filename, svg in first.items():
            with self.subTest(filename=filename):
                _ = ET.fromstring(svg)
        self.assertNotIn("Rank", first["stats-dark.svg"])
        self.assertIn("Xudong's GitHub Stats", first["stats-dark.svg"])
        self.assertIn(AVATAR_DATA_URI, first["stats-light.svg"])
        self.assertFalse(
            any(name.startswith(("overview-", "languages-")) for name in first)
        )

    def test_expected_refresh_sources_are_requested(self) -> None:
        fetcher = FakeFetcher(complete_responses())
        _ = build_assets(fetcher, username="xzAscC", now=NOW)
        decoded_requests = [unquote(url) for url in fetcher.requests]

        self.assertIn(
            "https://api.github.com/users/xzAscC/repos?type=owner&per_page=100&page=1",
            decoded_requests,
        )
        self.assertIn("https://api.github.com/users/xzAscC", decoded_requests)
        self.assertIn(
            "https://api.github.com/repos/GoXzascc/AbsTopK-SAE", decoded_requests
        )
        self.assertTrue(
            any(
                url.startswith("https://api.github.com/search/commits?")
                and "author:xzAscC" in url
                and "committer-date:2026-08-01..2026-08-31" in url
                for url in decoded_requests
            )
        )
        self.assertTrue(
            any(
                url.startswith("https://api.github.com/search/commits?")
                and "author:xzAscC" in url
                and "committer-date" not in url
                for url in decoded_requests
            )
        )
        self.assertIn("https://api.github.com/graphql", decoded_requests)
        self.assertIn(
            "https://avatars.githubusercontent.com/u/91479366?v=4&s=160",
            decoded_requests,
        )
        self.assertFalse(any("badges.strrl.dev" in url for url in decoded_requests))
        language_requests = [
            url for url in decoded_requests if url.endswith("/languages")
        ]
        self.assertEqual(language_requests, [])

    def test_authorization_is_only_sent_to_github(self) -> None:
        fetcher = FakeFetcher(complete_responses())
        with patch.dict(os.environ, {"GITHUB_TOKEN": "secret-token"}):
            _ = build_assets(fetcher, username="xzAscC", now=NOW)

        github_headers = [
            headers
            for url, headers in fetcher.request_headers
            if url.startswith("https://api.github.com/")
        ]
        self.assertTrue(github_headers)
        self.assertTrue(
            all(
                headers.get("Authorization") == "Bearer secret-token"
                for headers in github_headers
            )
        )
        avatar_headers = next(
            headers
            for url, headers in fetcher.request_headers
            if url.startswith("https://avatars.githubusercontent.com/")
        )
        self.assertNotIn("Authorization", avatar_headers)

    def test_avatar_must_come_from_github_and_be_an_image(self) -> None:
        account_url = "https://api.github.com/users/xzAscC"
        for avatar_url, payload in (
            ("https://evil.example/avatar.png", AVATAR_JPEG),
            ("https://avatars.githubusercontent.com/u/91479366?v=4", b"<html>"),
        ):
            with self.subTest(avatar_url=avatar_url):
                responses = complete_responses()
                account = json.loads(responses[account_url])
                account["avatar_url"] = avatar_url
                responses[account_url] = json_bytes(account)
                responses[f"{avatar_url}&s=160"] = payload
                with self.assertRaises(GenerationError):
                    _ = build_assets(
                        FakeFetcher(responses), username="xzAscC", now=NOW
                    )

    def test_exactly_one_thousand_repositories_is_supported(self) -> None:
        repository = repository_payload("xzAscC", "example")
        responses = {
            f"https://api.github.com/users/xzAscC/repos?type=owner&per_page=100&page={page}": json_bytes(
                [repository] * 100
            )
            for page in range(1, 11)
        }
        responses[
            "https://api.github.com/users/xzAscC/repos?type=owner&per_page=100&page=11"
        ] = json_bytes([])

        repositories = fetch_owned_repositories(
            FakeFetcher(responses), "xzAscC", {"User-Agent": "test"}
        )
        self.assertEqual(len(repositories), 1000)

    def test_search_commit_items_must_be_objects(self) -> None:
        url = (
            "https://api.github.com/search/commits?"
            "q=author%3AxzAscC+committer-date%3A2026-08-01..2026-08-31&per_page=1"
        )
        fetcher = FakeFetcher(
            {
                url: json_bytes(
                    {"total_count": 1, "incomplete_results": False, "items": ["bad"]}
                )
            }
        )
        with self.assertRaisesRegex(GenerationError, r"items\[0\]"):
            _ = fetch_monthly_commits(fetcher, "xzAscC", NOW, {"User-Agent": "test"})

    def test_oversized_response_is_rejected(self) -> None:
        responses = complete_responses()
        responses["https://api.github.com/repos/xzAscC/PostDyn"] = b" " * 1_000_001
        with self.assertRaisesRegex(GenerationError, "exceeds"):
            _ = build_assets(FakeFetcher(responses), username="xzAscC", now=NOW)

    def test_malformed_github_data_aborts_generation(self) -> None:
        responses = complete_responses()
        responses["https://api.github.com/repos/xzAscC/PostDyn"] = json_bytes(
            repository_payload("xzAscC", "PostDyn") | {"stargazers_count": "many"}
        )

        with self.assertRaisesRegex(GenerationError, "stargazers_count"):
            _ = build_assets(FakeFetcher(responses), username="xzAscC", now=NOW)

    def test_failed_refresh_leaves_every_existing_asset_untouched(self) -> None:
        responses = complete_responses()
        responses["https://api.github.com/graphql"] = b"broken"
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory) / "nested" / "assets"
            assets.mkdir(parents=True)
            for filename in ASSET_FILENAMES:
                _ = (assets / filename).write_text(f"old:{filename}", encoding="utf-8")

            with self.assertRaises(GenerationError):
                update_assets(
                    FakeFetcher(responses), assets, username="xzAscC", now=NOW
                )

            self.assertEqual(
                {
                    path.name: path.read_text(encoding="utf-8")
                    for path in assets.iterdir()
                },
                {filename: f"old:{filename}" for filename in ASSET_FILENAMES},
            )

    def test_successful_refresh_creates_missing_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory) / "nested" / "assets"
            update_assets(
                FakeFetcher(complete_responses()),
                assets,
                username="xzAscC",
                now=NOW,
            )
            self.assertEqual(
                {path.name for path in assets.iterdir()}, set(ASSET_FILENAMES)
            )

    def test_unchanged_refresh_does_not_rewrite_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory) / "assets"
            update_assets(
                FakeFetcher(complete_responses()), assets, username="xzAscC", now=NOW
            )
            before = {path.name: path.stat().st_mtime_ns for path in assets.iterdir()}
            update_assets(
                FakeFetcher(complete_responses()), assets, username="xzAscC", now=NOW
            )
            after = {path.name: path.stat().st_mtime_ns for path in assets.iterdir()}
            self.assertEqual(before, after)

    def test_replacement_failure_rolls_back_already_replaced_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory) / "assets"
            assets.mkdir()
            for filename in ASSET_FILENAMES:
                _ = (assets / filename).write_text(f"old:{filename}", encoding="utf-8")
            real_replace = os.replace
            replacement_count = 0

            def fail_second_replace(
                source: str | Path, destination: str | Path
            ) -> None:
                nonlocal replacement_count
                replacement_count += 1
                if replacement_count == 2:
                    raise OSError("injected replacement failure")
                real_replace(source, destination)

            with patch(
                "scripts.update_profile_assets.os.replace",
                side_effect=fail_second_replace,
            ):
                with self.assertRaisesRegex(
                    GenerationError, "injected replacement failure"
                ):
                    update_assets(
                        FakeFetcher(complete_responses()),
                        assets,
                        username="xzAscC",
                        now=NOW,
                    )

            self.assertEqual(
                {
                    path.name: path.read_text(encoding="utf-8")
                    for path in assets.iterdir()
                },
                {filename: f"old:{filename}" for filename in ASSET_FILENAMES},
            )


class TestRepositoryIntegration(unittest.TestCase):
    def test_checked_in_assets_exclude_low_contrast_light_accent(self) -> None:
        for filename in ASSET_FILENAMES:
            with self.subTest(filename=filename):
                svg = (ROOT / "assets" / filename).read_text(encoding="utf-8")
                self.assertNotIn("#0d9488", svg)

    def test_readme_uses_local_generated_assets_and_keeps_dimensions_and_links(
        self,
    ) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        image_sources: list[str] = []
        for attribute in ('src="', 'srcset="'):
            image_sources.extend(
                part.split('"', 1)[0] for part in readme.split(attribute)[1:]
            )

        self.assertFalse(
            any("github-readme-stats" in source for source in image_sources)
        )
        self.assertFalse(any("badges.strrl.dev" in source for source in image_sources))
        for filename in ASSET_FILENAMES:
            with self.subTest(filename=filename):
                self.assertTrue((ROOT / "assets" / filename).is_file())
                self.assertIn(f"./assets/{filename}", readme)
        for name in (
            "RobustDiM-PrefixSteering",
            "ProbingReflection",
            "PostDyn",
            "AbsTopK-SAE",
            "LLMUsage",
            "dotfiles",
        ):
            self.assertIn(f'height="120" alt="{name}"', readme)
        self.assertIn('src="./assets/stats-dark.svg" alt="GitHub Stats"', readme)
        self.assertNotIn("<table", readme)
        self.assertIn('href="https://github.com/GoXzascc/AbsTopK-SAE"', readme)
        self.assertIn("(prefers-color-scheme: dark)", readme)
        self.assertIn("(prefers-color-scheme: light)", readme)

    def test_readme_links_papers_under_research_without_duplicate_news(
        self,
    ) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        research = readme.split("### Research", 1)[1].split("###", 1)[0]

        self.assertNotIn("### News", readme)
        for link in (
            "https://openreview.net/forum?id=EEs6I4cO7S",
            "https://arxiv.org/abs/2506.12217",
            "https://xudongzhu.com/publications/",
        ):
            self.assertIn(f'href="{link}"', research)

    def test_readme_header_uses_plain_links_instead_of_remote_badges(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        header = readme.split("### Research", 1)[0]

        self.assertIn("<h1>Xudong Zhu</h1>", header)
        self.assertNotIn("img.shields.io", readme)
        for link in (
            "https://xudongzhu.com/",
            "mailto:zhu.3944@osu.edu",
            "https://scholar.google.com/citations?user=U55yracAAAAJ",
            "https://x.com/XudongZhu3944",
        ):
            self.assertIn(f'href="{link}"', header)

    def test_readme_ends_with_a_single_small_footer(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertNotIn("Acknowledgments", readme)
        self.assertTrue(readme.rstrip().endswith("</sub></p>"))
        self.assertIn("github-stats-extended", readme)

    def test_readme_pin_links_wrap_cards_without_underlined_whitespace(
        self,
    ) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        anchors = re.findall(r"<a [^>]*>(.*?)</a>", readme, flags=re.DOTALL)
        pins = [body for body in anchors if "<picture>" in body]

        self.assertEqual(len(pins), 6)
        for body in pins:
            self.assertEqual(body, body.strip())
            self.assertNotIn("\n", body)

    def test_workflow_refreshes_and_conditionally_stages_all_assets(self) -> None:
        workflow = (ROOT / ".github/workflows/update-profile-assets.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn('cron: "17 8 * * *"', workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn(
            "actions/checkout@93cb6efe18208431cddfb8368fd83d5badbf9bfd", workflow
        )
        self.assertIn("python3 -m scripts.update_profile_assets", workflow)
        self.assertIn("git diff --cached --quiet -- assets", workflow)
        self.assertIn("git add assets", workflow)
        self.assertLess(
            workflow.index("git add assets"),
            workflow.index("git diff --cached --quiet -- assets"),
        )
        self.assertIn('git commit -m "Update generated profile assets"', workflow)
        self.assertIn("exit 0", workflow)
        self.assertIn("concurrency:", workflow)


if __name__ == "__main__":
    _ = unittest.main()
