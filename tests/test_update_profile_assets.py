"""Deterministic tests for the complete profile asset generator."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote
from unittest.mock import patch

from scripts.update_profile_assets import (
    ASSET_FILENAMES,
    LIGHT_THEME,
    GenerationError,
    RepositoryStats,
    REPOSITORIES,
    build_assets,
    format_stat_number,
    render_link_bar,
    render_repository_card,
    update_assets,
)


ROOT = Path(__file__).resolve().parents[1]
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
    responses: dict[str, bytes] = {}
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
                assets = build_assets(FakeFetcher(responses))
                svg = assets[f"{spec.asset_stem}-dark.svg"]
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
        assets = build_assets(FakeFetcher(complete_responses()))
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

    def test_publications_link_bar_spans_the_card_grid(self) -> None:
        for dark, theme_title in ((False, "#404b91"), (True, "#aeb8ff")):
            with self.subTest(dark=dark):
                svg = render_link_bar(
                    "Full publication list", "xudongzhu.com/publications", dark=dark
                )
                root = ET.fromstring(svg)
                self.assertEqual(root.attrib["width"], "804")
                self.assertEqual(root.attrib["height"], "44")
                texts = {
                    node.attrib.get("class"): node.text
                    for node in root.iter(f"{SVG_NAMESPACE}text")
                }
                self.assertEqual(texts["label"], "Full publication list")
                self.assertEqual(texts["hint"], "xudongzhu.com/publications")
                self.assertEqual(css_fill(svg, "label"), theme_title)
                self.assertNotIn("→", svg)
                self.assertIn('class="arrow"', svg)

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
        first = build_assets(fetcher)
        second = build_assets(FakeFetcher(complete_responses()))

        self.assertEqual(tuple(first), ASSET_FILENAMES)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 14)
        self.assertEqual(
            {name for name in first if not name.startswith("pin-")},
            {"link-publications-light.svg", "link-publications-dark.svg"},
        )
        for filename, svg in first.items():
            with self.subTest(filename=filename):
                _ = ET.fromstring(svg)

    def test_only_pinned_repositories_are_requested(self) -> None:
        fetcher = FakeFetcher(complete_responses())
        _ = build_assets(fetcher)

        self.assertEqual(
            [unquote(url) for url in fetcher.requests],
            [
                f"https://api.github.com/repos/{spec.owner}/{spec.name}"
                for spec in REPOSITORIES
            ],
        )

    def test_authorization_is_only_sent_to_github(self) -> None:
        fetcher = FakeFetcher(complete_responses())
        with patch.dict(os.environ, {"GITHUB_TOKEN": "secret-token"}):
            _ = build_assets(fetcher)

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

    def test_oversized_response_is_rejected(self) -> None:
        responses = complete_responses()
        responses["https://api.github.com/repos/xzAscC/PostDyn"] = b" " * 1_000_001
        with self.assertRaisesRegex(GenerationError, "exceeds"):
            _ = build_assets(FakeFetcher(responses))

    def test_malformed_github_data_aborts_generation(self) -> None:
        responses = complete_responses()
        responses["https://api.github.com/repos/xzAscC/PostDyn"] = json_bytes(
            repository_payload("xzAscC", "PostDyn") | {"stargazers_count": "many"}
        )

        with self.assertRaisesRegex(GenerationError, "stargazers_count"):
            _ = build_assets(FakeFetcher(responses))

    def test_failed_refresh_leaves_every_existing_asset_untouched(self) -> None:
        responses = complete_responses()
        responses["https://api.github.com/repos/xzAscC/dotfiles"] = b"broken"
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory) / "nested" / "assets"
            assets.mkdir(parents=True)
            for filename in ASSET_FILENAMES:
                _ = (assets / filename).write_text(f"old:{filename}", encoding="utf-8")

            with self.assertRaises(GenerationError):
                update_assets(FakeFetcher(responses), assets)

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
            update_assets(FakeFetcher(complete_responses()), assets)
            self.assertEqual(
                {path.name for path in assets.iterdir()}, set(ASSET_FILENAMES)
            )

    def test_unchanged_refresh_does_not_rewrite_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory) / "assets"
            update_assets(FakeFetcher(complete_responses()), assets)
            before = {path.name: path.stat().st_mtime_ns for path in assets.iterdir()}
            update_assets(FakeFetcher(complete_responses()), assets)
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
                    update_assets(FakeFetcher(complete_responses()), assets)

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
        self.assertNotIn("./assets/stats-", readme)
        self.assertNotIn("<table", readme)
        self.assertIn('href="https://github.com/GoXzascc/AbsTopK-SAE"', readme)
        self.assertIn("(prefers-color-scheme: dark)", readme)
        self.assertIn("(prefers-color-scheme: light)", readme)

    def test_readme_research_ends_with_a_single_publications_link(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        research = readme.split("### Research", 1)[1].split("###", 1)[0]
        after_pins = research.split('alt="AbsTopK-SAE" /></picture></a>', 1)[1]

        self.assertNotIn("### News", readme)
        self.assertNotIn("Papers:", research)
        self.assertEqual(
            re.findall(r'href="([^"]+)"', after_pins),
            ["https://xudongzhu.com/publications/"],
        )
        self.assertIn('alt="Full publication list"', after_pins)
        self.assertIn("./assets/link-publications-light.svg", after_pins)

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

        self.assertEqual(len(pins), 7)
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
