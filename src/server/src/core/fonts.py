import json
import re
from dataclasses import dataclass
from pathlib import Path


FONT_ASSETS_DIR = (
    Path(__file__).resolve().parents[2] / "vite" / "assets" / "fonts"
)
FONT_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FONT_FAMILY_PATTERN = re.compile(r"^[A-Za-z0-9 ._-]+$")
FONT_CATEGORIES = {"serif", "sans-serif", "cursive", "monospace"}
TAILWIND_SANS = "var(--font-sans)"


@dataclass(frozen=True)
class ArticleFont:
    identifier: str
    label: str
    family: str
    category: str

    @property
    def css_stack(self):
        return f'"{self.family}", {self.category}'


def discover_article_fonts():
    """Return valid local fonts, skipping incomplete or unsafe manifests."""
    fonts = {}
    for manifest_path in sorted(FONT_ASSETS_DIR.glob("*/font.json")):
        try:
            manifest = json.loads(manifest_path.read_text())
            font = ArticleFont(
                identifier=manifest["id"],
                label=manifest["label"],
                family=manifest["family"],
                category=manifest["category"],
            )
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            continue

        if font.identifier != manifest_path.parent.name:
            continue
        if not FONT_ID_PATTERN.fullmatch(font.identifier):
            continue
        if not FONT_FAMILY_PATTERN.fullmatch(font.family):
            continue
        if not isinstance(font.label, str) or not font.label.strip():
            continue
        if font.category not in FONT_CATEGORIES:
            continue
        if not (manifest_path.parent / "font.css").is_file():
            continue
        fonts[font.identifier] = font
    return fonts


def article_font_choices():
    fonts = discover_article_fonts()
    return [("", "Tailwind Sans")] + [
        (font.identifier, font.label)
        for font in sorted(fonts.values(), key=lambda item: item.label.casefold())
    ]


def article_font_css_stack(identifier):
    font = discover_article_fonts().get(identifier)
    return font.css_stack if font else TAILWIND_SANS
