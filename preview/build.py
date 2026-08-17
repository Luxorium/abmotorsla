#!/usr/bin/env python3
"""Inline preview/index.html into one self-contained file.

The preview links the theme's real stylesheet so local editing stays in sync
with what ships. Publishing needs everything in one file, so this inlines the
CSS and the logo. Run: python3 preview/build.py
"""
import base64
import mimetypes
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "preview" / "index.html"
OUT = ROOT / "preview" / "dist" / "abmotors-preview.html"


def data_uri(path: pathlib.Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def main() -> None:
    html = SRC.read_text(encoding="utf-8")

    def inline_css(match: re.Match) -> str:
        href = match.group(1)
        css = (SRC.parent / href).resolve().read_text(encoding="utf-8")
        return f"<style>\n{css}\n</style>"

    html = re.sub(r'<link rel="stylesheet" href="([^"]+)">', inline_css, html)

    def inline_img(match: re.Match) -> str:
        src = match.group(1)
        return f'src="{data_uri((SRC.parent / src).resolve())}"'

    html = re.sub(r'src="(\.\./brand/[^"]+)"', inline_img, html)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}  ({len(html) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
