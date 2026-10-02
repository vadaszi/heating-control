"""Repository checks: the published files are self-contained.

- No file refers to the private development documents (they are not published):
  their file names, decision numbers or spec sections.
- Every relative link in the Markdown files points to an existing file and heading.
"""

from __future__ import annotations

import re
import subprocess
from functools import cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ("dev/", "CLAUDE.local.md")  # not published
TEXT_SUFFIXES = {".md", ".py", ".js", ".mjs", ".json", ".yaml", ".yml", ".toml", ".txt", ".cfg"}

# Built from parts, so this file does not match itself.
FORBIDDEN = {
    "a private document": re.compile(
        "|".join(
            re.escape(name)
            for name in (
                "design" + ".md",
                "implementation" + "-plan",
                "gemini" + "-review",
                "trial" + "-checklist",
                "bench" + "-checklist",
                "owner" + "-local",
            )
        )
    ),
    "a decision number": re.compile(r"\bD" + r"-\d{2,3}\b"),
    "a spec section": re.compile(chr(0xA7)),
}


@cache
def published_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return sorted(
        ROOT / name for name in listed if not name.startswith(PRIVATE) and (ROOT / name).is_file()
    )


def _text_files() -> list[Path]:
    return [path for path in published_files() if path.suffix in TEXT_SUFFIXES]


def test_no_reference_to_private_documents() -> None:
    problems = []
    for path in _text_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for what, pattern in FORBIDDEN.items():
                if pattern.search(line):
                    problems.append(f"{path.relative_to(ROOT)}:{number}: {what}: {line.strip()}")
    assert not problems, "\n".join(problems)


LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)|!\[[^\]]*\]\(([^)\s]+)\)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")


def _anchor(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)  # links -> their text
    text = text.replace("`", "").replace("*", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


@cache
def _anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    seen: dict[str, int] = {}
    fenced = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            fenced = not fenced
        if fenced or not (match := HEADING.match(line)):
            continue
        anchor = _anchor(match.group(2))
        count = seen.get(anchor, 0)
        seen[anchor] = count + 1
        anchors.add(anchor if count == 0 else f"{anchor}-{count}")
    return anchors


def _links(path: Path) -> list[str]:
    links = []
    fenced = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            fenced = not fenced
        if not fenced:
            links += [a or b for a, b in LINK.findall(line)]
    return links


@pytest.mark.parametrize(
    "path",
    [path for path in published_files() if path.suffix == ".md"],
    ids=lambda path: str(path.relative_to(ROOT)),
)
def test_relative_links_resolve(path: Path) -> None:
    problems = []
    for link in _links(path):
        if re.match(r"^[a-z][a-z0-9+.-]*:", link):  # https:, mailto:, ...
            continue
        target, _, anchor = link.partition("#")
        file = (path.parent / target).resolve() if target else path
        if not file.exists():
            problems.append(f"{link}: no such file")
        elif file.is_dir():
            problems.append(f"{link}: a folder, link a file in it")
        elif anchor and file.suffix == ".md" and anchor not in _anchors(file):
            problems.append(f"{link}: no heading #{anchor}")
        elif PRIVATE and str(file.relative_to(ROOT)).startswith(PRIVATE):
            problems.append(f"{link}: a private document")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(
    ("name", "size"),
    [("icon.png", 256), ("icon@2x.png", 512), ("dark_icon.png", 256), ("dark_icon@2x.png", 512)],
)
def test_brand_images(name: str, size: int) -> None:
    """Home Assistant shows the integration's icon from its `brand` folder."""
    data = (
        ROOT / "custom_components" / "multizone_floor_heating_manager" / "brand" / name
    ).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    assert (width, height) == (size, size)
