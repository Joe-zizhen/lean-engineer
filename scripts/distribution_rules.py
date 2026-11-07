import os
import stat


EXCLUDED_PARTS = {"dist", ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "backup", "backups", "node_modules", ".venv"}
EXCLUDED_NAMES = {".DS_Store"}
EXCLUDED_SUFFIXES = {".pyc", ".orig", ".bak", ".tmp", ".swp"}


def is_distribution_excluded(relative):
    return any(part in EXCLUDED_PARTS for part in relative.parts) or relative.name in EXCLUDED_NAMES or relative.suffix in EXCLUDED_SUFFIXES


def is_link(path):
    if path.is_symlink():
        return True
    is_junction = getattr(os.path, "isjunction", None)
    if is_junction and is_junction(path):
        return True
    if os.name != "nt":
        return False
    try:
        attributes = os.lstat(path).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def has_distribution_link(root, relative):
    current = root
    for part in relative.parts:
        current = current / part
        if is_link(current):
            return True
    return False
