#!/usr/bin/env python3
import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

from distribution_rules import has_distribution_link, is_distribution_excluded, is_link
from validate_package import validate_package

DISTRIBUTION_FILES = {"plugin.json", "kimi.plugin.json", "SKILL.md", "README.md", "LICENSE", "NOTICE", "VALIDATION.md"}
DISTRIBUTION_DIRECTORIES = {".claude-plugin", "agents", "evals", "scripts", "skills", "tests"}


def require_within_root(root, path):
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"package path escapes plugin root: {path}") from exc
    return resolved


def package_files(root):
    root = root.resolve()
    files = []
    for name in sorted(DISTRIBUTION_FILES):
        path = root / name
        if not path.exists():
            continue
        resolved = require_within_root(root, path)
        if is_link(path) or not resolved.is_file():
            raise ValueError(f"invalid distribution file: {path}")
        files.append((resolved, path.relative_to(root)))
    for name in sorted(DISTRIBUTION_DIRECTORIES):
        base = root / name
        if not base.exists():
            continue
        for path in base.rglob("*"):
            relative = path.relative_to(root)
            if is_distribution_excluded(relative):
                continue
            resolved = require_within_root(root, path)
            if has_distribution_link(root, relative):
                raise ValueError(f"linked paths are not packaged: {relative}")
            if resolved.is_file():
                files.append((resolved, relative))
    return sorted(files, key=lambda item: item[1].as_posix())


def validate_output(root, output, files):
    root = root.resolve()
    output = output.resolve()
    try:
        relative = output.relative_to(root)
    except ValueError:
        relative = None
    if relative is not None and (not relative.parts or relative.parts[0] != "dist"):
        raise ValueError("output inside plugin root must be under dist/")
    input_paths = {path.resolve() for path, _ in files}
    if output in input_paths:
        raise ValueError("output would overwrite an input file")
    if output.exists() and output.is_dir():
        raise ValueError("output is a directory")
    return output


def distribution_entries(files, skill_only):
    required = ("plugin.json", "kimi.plugin.json", ".claude-plugin/plugin.json")
    if skill_only:
        prefix = Path("skills/lean-engineer")
        files = [
            (path, Path("lean-engineer") / relative.relative_to(prefix))
            for path, relative in files
            if relative.parts[:2] == prefix.parts
        ]
        required = ("lean-engineer/SKILL.md", "lean-engineer/LICENSE", "lean-engineer/NOTICE")
    return files, required


def validate_archive(temporary, required):
    with zipfile.ZipFile(temporary) as archive:
        names = archive.namelist()
        for name in required:
            if name not in names:
                raise ValueError(f"required distribution file is missing: {name}")
        if any(name.startswith(("/", "dist/")) or ".." in Path(name).parts for name in names):
            raise ValueError("unsafe or excluded ZIP entry")
        bad = archive.testzip()
        if bad:
            raise ValueError(f"corrupt ZIP entry: {bad}")


def write_zip(root, output, skill_only=False):
    root = root.resolve()
    files = package_files(root)
    output = validate_output(root, output, files)
    files, required = distribution_entries(files, skill_only)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    if temporary.exists():
        raise ValueError(f"temporary output already exists: {temporary}")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path, relative in files:
                info = zipfile.ZipInfo(relative.as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                with path.open("rb") as source, archive.open(info, "w") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
        validate_archive(temporary, required)
        temporary.replace(output)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return len(files)


def hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Package lean-engineer without third-party dependencies")
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--format", choices=("plugin", "skill"), default="plugin")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        validation = validate_package(root)
        if not validation["ok"]:
            print(json.dumps(validation, ensure_ascii=False, indent=2))
            return 1
        manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
        prefix = "lean-engineer-skill" if args.format == "skill" else "lean-engineer"
        output = args.output or root / "dist" / f"{prefix}-{manifest['version']}.zip"
        if not output.is_absolute():
            output = (Path.cwd() / output).resolve()
        count = write_zip(root, output, skill_only=args.format == "skill")
        result = {
            "ok": True,
            "errors": [],
            "warnings": [],
            "summary": {
                "output": str(output),
                "files": count,
                "bytes": output.stat().st_size,
                "sha256": hash_file(output),
            },
        }
    except (OSError, ValueError, TypeError, zipfile.BadZipFile) as exc:
        result = {"ok": False, "errors": [str(exc)], "warnings": [], "summary": {}}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
