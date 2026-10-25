"""Tag-list cleanup."""
from __future__ import annotations

import re


def process_tags(text: str) -> str:
    """'white dog, a huge bike, 1girl'  ->  'white_dog a_huge_bike 1girl'

    * commas separate tags; spaces inside a tag become underscores
    * all commas are removed and tags are joined by single spaces
    * stray / repeated whitespace is cleaned; each line is processed on its own
    """
    lines = []
    for line in text.splitlines():
        tags = []
        for part in line.split(","):
            part = re.sub(r"\s+", " ", part).strip()
            if part:
                tags.append(part.replace(" ", "_"))
        lines.append(" ".join(tags))
    return "\n".join(lines)
