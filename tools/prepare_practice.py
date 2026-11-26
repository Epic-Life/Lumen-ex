"""Create a fresh practice copy whose bytes match the Team Kit answer key."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", type=Path, default=Path("dev"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = (args.kit / "input").resolve(strict=True)
    output = args.output.resolve()
    if output.exists() or output.is_relative_to(source):
        parser.error("output must be a new directory outside the inputs")
    truth = json.loads((args.kit / "truth/truth.json").read_text(encoding="utf-8"))
    restored = {}
    converted = 0
    for name, metadata in truth["files"].items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ValueError("invalid practice path")
        original = (source / path).resolve(strict=True)
        original.relative_to(source)
        content = original.read_bytes()
        lf = content.replace(b"\r\n", b"\n")
        for candidate in (content, lf, lf.replace(b"\n", b"\r\n")):
            if hashlib.sha256(candidate).hexdigest() == metadata["sha256"]:
                restored[path] = candidate
                converted += candidate != content
                break
        else:
            raise ValueError("practice bytes differ from the kit beyond line endings")
    output.mkdir(parents=True)
    for path, content in restored.items():
        destination = output / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    print(f"Verified {len(restored)} practice files, restored line endings in {converted}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
