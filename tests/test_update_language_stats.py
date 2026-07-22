"""Tests for scripts/update_language_stats.py.

Run from project root:
    python3 -m unittest discover -s tests -v

These tests cover the pure functions only; network I/O
(``fetch_repositories``, ``fetch_repository_languages``) is exercised by
the weekly GitHub Actions workflow.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.update_language_stats import (
    aggregate_weights,
    displayed_languages,
    render_svg,
    write_if_changed,
)


class TestAggregateWeights(unittest.TestCase):
    """aggregate_weights: per-repo proportional split, summed across repos.

    Each non-empty repo contributes a total weight of 1, distributed across
    its languages proportional to the byte counts returned by GitHub's
    ``/repos/{owner}/{repo}/languages`` endpoint.
    """

    def test_empty_list_returns_empty_dict(self) -> None:
        self.assertEqual(aggregate_weights([]), {})

    def test_single_repo_single_language_gets_weight_one(self) -> None:
        result = aggregate_weights([{"Python": 1000}])
        self.assertEqual(result, {"Python": 1.0})

    def test_single_repo_proportional_split(self) -> None:
        # Bytes: Python 8000, JavaScript 2000 → 80% / 20%
        result = aggregate_weights([{"Python": 8000, "JavaScript": 2000}])
        self.assertAlmostEqual(result["Python"], 0.8, places=10)
        self.assertAlmostEqual(result["JavaScript"], 0.2, places=10)

    def test_two_repos_weights_sum_correctly(self) -> None:
        # Repo A: Python 100%        → Python +1.0
        # Repo B: Python 50% / TeX 50% → Python +0.5, TeX +0.5
        # Totals: Python 1.5, TeX 0.5
        result = aggregate_weights([{"Python": 1000}, {"Python": 500, "TeX": 500}])
        self.assertAlmostEqual(result["Python"], 1.5, places=10)
        self.assertAlmostEqual(result["TeX"], 0.5, places=10)

    def test_empty_repo_dict_skipped(self) -> None:
        result = aggregate_weights([{}, {"Python": 1000}])
        self.assertEqual(result, {"Python": 1.0})

    def test_zero_total_bytes_skipped(self) -> None:
        # Defensive: a repo whose byte counts sum to zero must be skipped
        # to avoid a division-by-zero. Should not happen in practice.
        result = aggregate_weights([{"Python": 0, "JavaScript": 0}])
        self.assertEqual(result, {})

    def test_each_repo_contributes_total_weight_one(self) -> None:
        # Property test: sum of all weights equals number of non-empty repos,
        # regardless of how languages are distributed inside each repo.
        repos = [
            {"Python": 8000, "JavaScript": 2000},
            {"TeX": 100, "HTML": 100, "CSS": 100},
            {"Python": 1, "Rust": 9999},
        ]
        result = aggregate_weights(repos)
        self.assertAlmostEqual(sum(result.values()), 3.0, places=10)


class TestDisplayedLanguages(unittest.TestCase):
    """displayed_languages: rank by weight, collapse tail into 'Other'."""

    def test_empty_input_returns_empty_list(self) -> None:
        self.assertEqual(displayed_languages({}), [])

    def test_single_language_returned_as_is(self) -> None:
        result = displayed_languages({"Python": 1.5})
        self.assertEqual(result, [("Python", 1.5)])

    def test_sorted_by_weight_descending(self) -> None:
        weights = {"Python": 1.5, "TeX": 0.7, "JavaScript": 0.2}
        result = displayed_languages(weights)
        self.assertEqual(
            [name for name, _ in result],
            ["Python", "TeX", "JavaScript"],
        )

    def test_six_or_fewer_all_kept_no_other_bucket(self) -> None:
        weights = {f"Lang{i}": float(6 - i) for i in range(6)}
        result = displayed_languages(weights)
        self.assertEqual(len(result), 6)
        self.assertFalse(any(name == "Other" for name, _ in result))

    def test_more_than_six_collapsed_to_other(self) -> None:
        # 10 languages: top 5 kept, ranks 6-10 collapsed into "Other".
        weights = {f"Lang{i}": float(10 - i) for i in range(10)}
        result = displayed_languages(weights)
        self.assertEqual(len(result), 6)
        self.assertEqual(result[-1][0], "Other")
        # Top 5 by descending weight: Lang0..Lang4
        self.assertEqual(
            [name for name, _ in result[:5]],
            ["Lang0", "Lang1", "Lang2", "Lang3", "Lang4"],
        )
        # "Other" weight must equal the sum of ranks 6..10.
        expected_other = sum(float(10 - i) for i in range(5, 10))
        self.assertAlmostEqual(result[-1][1], expected_other, places=10)

    def test_seven_languages_collapses_only_last_two(self) -> None:
        weights = {f"Lang{i}": float(7 - i) for i in range(7)}
        result = displayed_languages(weights)
        self.assertEqual(
            result,
            [
                ("Lang0", 7.0),
                ("Lang1", 6.0),
                ("Lang2", 5.0),
                ("Lang3", 4.0),
                ("Lang4", 3.0),
                ("Other", 3.0),
            ],
        )

    def test_preferred_order_breaks_weight_ties(self) -> None:
        # Equal weights: Python (preferred index 0) ranks before TeX (index 1).
        weights = {"TeX": 1.0, "Python": 1.0}
        result = displayed_languages(weights)
        self.assertEqual(result[0][0], "Python")

    def test_alphabetical_breaks_remaining_ties(self) -> None:
        # Two non-preferred languages, equal weight: casefold alphabetical.
        weights = {"Zebra": 1.0, "Apple": 1.0}
        result = displayed_languages(weights)
        self.assertEqual([name for name, _ in result], ["Apple", "Zebra"])


class TestRenderSvg(unittest.TestCase):
    """render_svg: SVG output structure, percentages, theme, colors."""

    def test_empty_entries_raises_runtime_error(self) -> None:
        with self.assertRaises(RuntimeError):
            render_svg([], dark=False)

    def test_contains_title_text(self) -> None:
        svg = render_svg([("Python", 1.0)], dark=False)
        self.assertIn("Top Languages by Repository", svg)

    def test_percentages_formatted_to_two_decimals(self) -> None:
        svg = render_svg([("Python", 0.75), ("TeX", 0.25)], dark=False)
        self.assertIn("75.00%", svg)
        self.assertIn("25.00%", svg)

    def test_unknown_language_uses_fallback_color(self) -> None:
        svg = render_svg([("FictionalLang", 1.0)], dark=False)
        self.assertEqual(svg.count('fill="#64748b"'), 2)

    def test_dark_and_light_themes_use_expected_colors(self) -> None:
        dark = render_svg([("Python", 1.0)], dark=True)
        light = render_svg([("Python", 1.0)], dark=False)
        self.assertIn("fill: #5eead4", dark)
        self.assertIn("fill: #94a3b8", dark)
        self.assertIn("fill: #0d9488", light)
        self.assertIn("fill: #334155", light)

    def test_desc_lists_each_language_percentage(self) -> None:
        svg = render_svg([("Python", 0.5), ("TeX", 0.5)], dark=False)
        self.assertIn(
            '<desc id="desc">Python 50.00 percent; TeX 50.00 percent.</desc>',
            svg,
        )

    def test_long_language_name_is_truncated_in_legend_only(self) -> None:
        name = "SomeVeryLongLanguageName"
        svg = render_svg([(name, 1.0)], dark=False)
        self.assertIn("SomeVeryLongLan… 100.00%", svg)
        self.assertIn(f"{name} 100.00 percent", svg)

    def test_output_is_well_formed_svg(self) -> None:
        svg = render_svg([("Python", 1.0)], dark=False)
        root = ET.fromstring(svg)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")


class TestWriteIfChanged(unittest.TestCase):
    """write_if_changed: idempotent writer to avoid spurious git diffs."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_writes_new_file_when_missing(self) -> None:
        path = self.tmp_path / "out.svg"
        write_if_changed(path, "<svg/>")
        self.assertEqual(path.read_text(encoding="utf-8"), "<svg/>")

    def test_overwrites_when_content_differs(self) -> None:
        path = self.tmp_path / "out.svg"
        path.write_text("old", encoding="utf-8")
        write_if_changed(path, "new")
        self.assertEqual(path.read_text(encoding="utf-8"), "new")

    def test_preserves_file_when_content_unchanged(self) -> None:
        # Critical for the workflow: identical content must NOT trigger a
        # rewrite, otherwise the weekly commit is polluted by no-op bumps
        # in mtime (and potentially byte-identical rewrites on weird FSes).
        path = self.tmp_path / "out.svg"
        path.write_text("same", encoding="utf-8")
        mtime_before = os.path.getmtime(path)
        # Sleep past filesystem mtime granularity (HFS+ is 1s; ext4 is ns).
        time.sleep(0.05)
        write_if_changed(path, "same")
        mtime_after = os.path.getmtime(path)
        self.assertEqual(mtime_before, mtime_after)


if __name__ == "__main__":
    unittest.main()
