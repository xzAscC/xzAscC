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
    MetricBadge,
    RepositoryStats,
    build_assets,
    calculate_rank,
    fetch_monthly_commits,
    fetch_owned_repositories,
    format_stat_number,
    parse_visit_value,
    render_account_card,
    render_badge,
    render_repository_card,
    update_assets,
)
from scripts.update_language_stats import render_svg as render_language_svg


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
SVG_NAMESPACE = "{http://www.w3.org/2000/svg}"


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
                "name": "Xudong Zhu",
                "public_repos": 31,
                "followers": 42,
                "created_at": "2018-09-01T00:00:00Z",
            }
        ),
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
                        "followers": {"totalCount": 42},
                        "repositoriesContributedTo": {"totalCount": 18},
                    }
                }
            }
        ),
        "https://badges.strrl.dev/visits/xzAscC/xzAscC?style=flat-square&color=black&logo=github&v=2": (
            b'<svg xmlns="http://www.w3.org/2000/svg" role="img" '
            b'aria-label="visits: 12.3k"><title>visits: 12.3k</title></svg>'
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
    for repository in repositories:
        language_url = repository["languages_url"]
        if not isinstance(language_url, str):
            raise AssertionError("Fixture languages_url must be a string")
        responses[language_url] = json_bytes({"Python": 800, "TeX": 200})
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

        for dark, expected_border in ((False, "#e2e8f0"), (True, "#1e3a32")):
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
        self.assertIn("#5eead4", dark)
        self.assertIn("#94a3b8", dark)
        self.assertIn("#0d1117", dark)
        self.assertIn("#0f766e", light)
        self.assertIn("#334155", light)
        self.assertIn("#ffffff", light)
        self.assertGreaterEqual(dark.count('class="icon"'), 3)

    def test_account_card_matches_github_stats_extended_layout(self) -> None:
        account = AccountStats(
            username="xzAscC",
            total_stars=53,
            total_commits=10700,
            total_prs=1700,
            total_issues=22,
            contributed_to=18,
            rank_level="A",
            rank_percentile=24.0,
        )
        dark = render_account_card(account, dark=True)
        light = render_account_card(account, dark=False)

        for svg in (dark, light):
            root = ET.fromstring(svg)
            self.assertEqual(root.attrib["width"], "419")
            self.assertEqual(root.attrib["height"], "195")
            self.assertIn("XzAscC's GitHub Stats", svg)
            self.assertIn("Total Stars Earned", svg)
            self.assertIn("Total Commits", svg)
            self.assertIn("Total PRs", svg)
            self.assertIn("Total Issues", svg)
            self.assertIn("Contributed to (last year)", svg)
            self.assertIn("10.7k", svg)
            self.assertIn("1.7k", svg)
            self.assertIn(">53<", svg)
            self.assertIn(">22<", svg)
            self.assertIn(">18<", svg)
            self.assertIn(">A<", svg)
            self.assertIn("rank-circle", svg)
            self.assertIn('class="icon"', svg)
            self.assertNotIn("Commits This Month", svg)
            self.assertNotIn("Public Repositories", svg)
            self.assertNotIn("Followers", svg)
        self.assertIn("#14b8a6", dark)
        self.assertIn("#0f766e", light)
        self.assertNotEqual(dark, light)

    def test_calculate_rank_matches_github_stats_extended(self) -> None:
        rank = calculate_rank(
            all_commits=True,
            commits=10700,
            prs=1700,
            issues=22,
            reviews=0,
            stars=138,
            followers=33,
        )
        self.assertEqual(rank.level, "A")
        self.assertGreater(rank.percentile, 0)
        self.assertLessEqual(rank.percentile, 100)

    def test_format_stat_number_uses_short_k_suffix(self) -> None:
        self.assertEqual(format_stat_number(999), "999")
        self.assertEqual(format_stat_number(1000), "1k")
        self.assertEqual(format_stat_number(10700), "10.7k")
        self.assertEqual(format_stat_number(1700), "1.7k")

    def test_badge_is_valid_accessible_and_deterministic(self) -> None:
        badge = MetricBadge(label="commits/month", value="19")
        first = render_badge(badge)
        second = render_badge(badge)
        root = ET.fromstring(first)

        self.assertEqual(first, second)
        self.assertEqual(root.attrib["height"], "20")
        self.assertEqual(root.attrib["aria-label"], "commits/month: 19")
        self.assertIn("<title>commits/month: 19</title>", first)
        self.assertIn("#0d1117", first)

    def test_all_small_light_theme_text_meets_wcag_aa_contrast(self) -> None:
        repository = render_repository_card(
            RepositoryStats("xzAscC/example", "Description", 1, 1, "Python"),
            dark=False,
        )
        account = render_account_card(
            AccountStats("xzAscC", 1, 1, 1, 1, 1, "C", 100.0), dark=False
        )
        language = render_language_svg([("Python", 1.0)], dark=False)
        badge = render_badge(MetricBadge("visits", "100"))
        badge_root = ET.fromstring(badge)
        badge_stops = [
            element.attrib["stop-color"]
            for element in badge_root.iter(f"{SVG_NAMESPACE}stop")
        ]
        badge_rects = list(badge_root.iter(f"{SVG_NAMESPACE}rect"))
        badge_group = next(badge_root.iter(f"{SVG_NAMESPACE}g"))
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
            ("account heading", css_fill(account, "heading"), LIGHT_THEME.background),
            ("account stat", css_fill(account, "stat"), LIGHT_THEME.background),
            ("language heading", css_fill(language, "heading"), "#ffffff"),
            ("language label", css_fill(language, "label"), "#ffffff"),
            ("badge label gradient top", badge_group.attrib["fill"], badge_stops[0]),
            ("badge label gradient bottom", badge_group.attrib["fill"], badge_stops[1]),
            ("badge value", badge_group.attrib["fill"], badge_rects[1].attrib["fill"]),
        )
        for label, foreground, background in pairs:
            with self.subTest(label=label):
                self.assertGreaterEqual(
                    contrast_ratio(foreground, background),
                    4.5,
                    f"{label}: {foreground} on {background}",
                )


class TestVisitParsing(unittest.TestCase):
    def test_extracts_only_sanitized_display_value(self) -> None:
        payload = (
            b'<svg xmlns="http://www.w3.org/2000/svg">'
            b"<title>visits: 12.3k</title><script>ignored()</script></svg>"
        )
        self.assertEqual(parse_visit_value(payload), "12.3k")

    def test_rejects_malformed_or_unsafe_values(self) -> None:
        cases = (
            b"not xml",
            b'<svg xmlns="http://www.w3.org/2000/svg"><title>visits</title></svg>',
            b'<svg xmlns="http://www.w3.org/2000/svg"><title>visits: &lt;x&gt;</title></svg>',
            b'<svg xmlns="http://www.w3.org/2000/svg"><title>stars: 12</title></svg>',
            b"<html><title>visits: 12</title></html>",
        )
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(GenerationError):
                    _ = parse_visit_value(payload)


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
        self.assertEqual(len(first), 20)
        for filename, svg in first.items():
            with self.subTest(filename=filename):
                _ = ET.fromstring(svg)

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
            "https://badges.strrl.dev/visits/xzAscC/xzAscC?style=flat-square&color=black&logo=github&v=2",
            decoded_requests,
        )
        language_requests = [
            url for url in decoded_requests if url.endswith("/languages")
        ]
        self.assertEqual(len(language_requests), 5)

    def test_authorization_is_only_sent_to_github(self) -> None:
        fetcher = FakeFetcher(complete_responses())
        with patch.dict(os.environ, {"GITHUB_TOKEN": "secret-token"}):
            _ = build_assets(fetcher, username="xzAscC", now=NOW)

        github_headers = [
            headers
            for url, headers in fetcher.request_headers
            if url.startswith("https://api.github.com/")
        ]
        visits_headers = next(
            headers
            for url, headers in fetcher.request_headers
            if url.startswith("https://badges.strrl.dev/")
        )
        self.assertTrue(github_headers)
        self.assertTrue(
            all(
                headers.get("Authorization") == "Bearer secret-token"
                for headers in github_headers
            )
        )
        self.assertNotIn("Authorization", visits_headers)

    def test_non_github_languages_url_is_rejected_before_fetch(self) -> None:
        responses = complete_responses()
        repository = repository_payload("xzAscC", "unsafe")
        repository["languages_url"] = "https://example.test/token-target"
        responses[
            "https://api.github.com/users/xzAscC/repos?type=owner&per_page=100&page=1"
        ] = json_bytes([repository])

        with self.assertRaisesRegex(GenerationError, "languages_url"):
            _ = build_assets(FakeFetcher(responses), username="xzAscC", now=NOW)

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
        responses[
            "https://badges.strrl.dev/visits/xzAscC/xzAscC?style=flat-square&color=black&logo=github&v=2"
        ] = b"broken"
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

    def test_empty_language_data_is_controlled_and_leaves_assets_untouched(
        self,
    ) -> None:
        responses = complete_responses()
        responses[
            "https://api.github.com/users/xzAscC/repos?type=owner&per_page=100&page=1"
        ] = json_bytes([])
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory) / "assets"
            assets.mkdir()
            for filename in ASSET_FILENAMES:
                _ = (assets / filename).write_text(f"old:{filename}", encoding="utf-8")

            with self.assertRaisesRegex(
                GenerationError, "No public repository languages were found"
            ):
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
                self.assertIn(f"./assets/{filename}", readme)
                self.assertTrue((ROOT / "assets" / filename).is_file())
        for name in (
            "RobustDiM-PrefixSteering",
            "ProbingReflection",
            "PostDyn",
            "AbsTopK-SAE",
            "LLMUsage",
            "dotfiles",
        ):
            self.assertIn(f'height="120" alt="{name}"', readme)
        self.assertIn('width="419" height="195" alt="GitHub Stats"', readme)
        self.assertIn('href="https://github.com/GoXzascc/AbsTopK-SAE"', readme)
        self.assertIn("(prefers-color-scheme: dark)", readme)
        self.assertIn("(prefers-color-scheme: light)", readme)

    def test_workflow_refreshes_and_conditionally_stages_all_assets(self) -> None:
        workflow = (ROOT / ".github/workflows/update-language-stats.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn('cron: "17 8 * * 1"', workflow)
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
