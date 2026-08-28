"""
Guards the tag palette against WCAG 2.1 regressions.

Colours are easy to tweak by eye and hard to eyeball for contrast, so the
thresholds are asserted directly against the stylesheets:

  1.4.11 Non-text Contrast  chip fill vs. row background  >= 3:1
  1.4.3  Contrast (Minimum) label vs. chip fill           >= 4.5:1

Pure arithmetic over the CSS text — no PyGObject, so this runs on CI.
"""

import re
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "src" / "meeting_recorder" / "assets"
DARK_CSS = ASSETS / "style.css"
LIGHT_CSS = ASSETS / "style-light.css"

# The row background a chip actually sits on, per libadwaita's boxed list.
DARK_ROW = "#303030"
LIGHT_ROW = "#FFFFFF"

TAGS = ("blue", "green", "yellow", "orange", "red", "purple", "brown")


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (_channel(int(h[i : i + 2], 16)) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def defined_colors(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    return dict(re.findall(r"@define-color\s+(\w+)\s+(#[0-9A-Fa-f]{6})\s*;", text))


@pytest.fixture(scope="module")
def dark():
    return defined_colors(DARK_CSS)


@pytest.fixture(scope="module")
def light():
    # The light sheet overrides only the colours it redefines.
    merged = defined_colors(DARK_CSS)
    merged.update(defined_colors(LIGHT_CSS))
    return merged


class TestContrastMath:
    def test_known_reference_ratios(self):
        # Sanity-check the implementation against WCAG's own worked examples.
        assert contrast("#FFFFFF", "#000000") == pytest.approx(21.0, abs=0.01)
        assert contrast("#777777", "#FFFFFF") == pytest.approx(4.48, abs=0.02)


class TestPaletteIsComplete:
    def test_dark_sheet_defines_every_tag(self, dark):
        for tag in TAGS:
            assert f"tag_{tag}_bg" in dark, tag
            assert f"tag_{tag}_fg" in dark, tag

    def test_light_sheet_overrides_every_tag(self):
        overrides = defined_colors(LIGHT_CSS)
        for tag in TAGS:
            assert f"tag_{tag}_bg" in overrides, tag
            assert f"tag_{tag}_fg" in overrides, tag


class TestWcagNonTextContrast:
    """1.4.11 — the chip must be discernible against the row behind it."""

    @pytest.mark.parametrize("tag", TAGS)
    def test_dark_theme_chip_against_row(self, dark, tag):
        ratio = contrast(dark[f"tag_{tag}_bg"], DARK_ROW)
        assert ratio >= 3.0, f"{tag}: {ratio:.2f}:1 against {DARK_ROW}"

    @pytest.mark.parametrize("tag", TAGS)
    def test_light_theme_chip_against_row(self, light, tag):
        ratio = contrast(light[f"tag_{tag}_bg"], LIGHT_ROW)
        assert ratio >= 3.0, f"{tag}: {ratio:.2f}:1 against {LIGHT_ROW}"


class TestWcagTextContrast:
    """1.4.3 — the tag name must be readable on its chip."""

    @pytest.mark.parametrize("tag", TAGS)
    def test_dark_theme_label_on_chip(self, dark, tag):
        ratio = contrast(dark[f"tag_{tag}_fg"], dark[f"tag_{tag}_bg"])
        assert ratio >= 4.5, f"{tag}: {ratio:.2f}:1"

    @pytest.mark.parametrize("tag", TAGS)
    def test_light_theme_label_on_chip(self, light, tag):
        ratio = contrast(light[f"tag_{tag}_fg"], light[f"tag_{tag}_bg"])
        assert ratio >= 4.5, f"{tag}: {ratio:.2f}:1"
