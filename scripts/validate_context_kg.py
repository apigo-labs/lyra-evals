from __future__ import annotations

import re
import sys
from pathlib import Path

FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    pages = {path.stem: path for path in root.rglob("*.md")}
    index = (root / "_meta" / "index.md").read_text(encoding="utf-8")
    for stem, path in pages.items():
        text = path.read_text(encoding="utf-8")
        match = FRONTMATTER.match(text)
        if not match:
            errors.append(f"{path}: missing frontmatter")
            continue
        metadata = match.group(1)
        for field in ("title:", "tags:", "links:", "updated:", "sources:"):
            if field not in metadata:
                errors.append(f"{path}: missing {field[:-1]}")
        if (
            path.parent.name != "_meta"
            and stem not in {"todo", "lessons"}
            and f"[[{stem}]]" not in index
        ):
            errors.append(f"{path}: missing from index")
        for target in re.findall(r"\[\[([a-z0-9-]+)\]\]", text):
            if target not in pages:
                errors.append(f"{path}: broken link {target}")
    return errors


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "context-kg")
    errors = validate(root)
    if errors:
        print("\n".join(errors))
        raise SystemExit(1)
    print("context-kg validation passed")


if __name__ == "__main__":
    main()
