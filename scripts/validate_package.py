#!/usr/bin/env python3
import argparse
from datetime import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

from distribution_rules import has_distribution_link, is_distribution_excluded

REQUIRED_FILES = [
    "plugin.json",
    "kimi.plugin.json",
    ".claude-plugin/plugin.json",
    "LICENSE",
    "SKILL.md",
    "README.md",
    "NOTICE",
    "VALIDATION.md",
    "agents/lean-reviewer.md",
    "agents/openai.yaml",
    "evals/tasks.json",
    "evals/README.md",
    "evals/runs.json",
    "scripts/distribution_rules.py",
    "skills/lean-engineer/SKILL.md",
    "skills/lean-engineer/NOTICE",
    "skills/lean-engineer/LICENSE",
    "skills/lean-engineer/agents/openai.yaml",
    "skills/lean-engineer/assets/backend-review-template.json",
    "skills/lean-engineer/scripts/validate_review.py",
]
CORE_REFERENCES = [
    "implementation.md",
    "reusable-capabilities.md",
    "risk-gates.md",
    "verification.md",
    "review-protocol.md",
    "security-diff-review.md",
    "backend-review.md",
    "frontend-review.md",
    "formal-verification.md",
    "tooling.md",
]
BACKEND_REFERENCES = [
    "api-contracts.md",
    "identity-access.md",
    "data-consistency.md",
    "concurrency-messaging.md",
    "security-runtime.md",
    "delivery-operations.md",
    "ledger-contract.md",
]
FRONTEND_REFERENCES = [
    "routes-components.md",
    "state-data.md",
    "accessibility-layout.md",
    "security-privacy.md",
    "performance-lifecycle.md",
    "delivery-evidence.md",
]
DOMAIN_REFERENCES = ["mobile.md", "embedded.md", "data-engineering.md"]
TOPICS = {
    "api_contracts",
    "identity_access",
    "data_consistency",
    "concurrency_messaging",
    "security_runtime",
    "delivery_operations",
}
ALLOWED_EVAL_SKILLS = {
    "lean-engineer",
    "lean-reviewer",
}
LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
ABSOLUTE_PATH_PATTERN = re.compile(r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|/(?:Users|home|tmp|etc|var)/)")
FIELD_PATTERN = re.compile(r"^\s*(name|description|license|type|whenToUse|disableModelInvocation|version):\s*(.*)$")


def parse_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing YAML frontmatter")
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as exc:
        raise ValueError("unterminated YAML frontmatter") from exc
    fields = {}
    for line in lines[1:end]:
        match = FIELD_PATTERN.match(line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip()
        quoted = len(value) >= 2 and value[0] == value[-1] == '"'
        if quoted:
            value = json.loads(value)
        if key == "disableModelInvocation" and not quoted and value.lower() in {"true", "false"}:
            value = value.lower() == "true"
        fields[key] = value
    return fields, lines[end + 1 :]


def validate_skill_file(path, errors):
    try:
        fields, body = parse_frontmatter(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: {exc}")
        return {}
    name = fields.get("name")
    description = fields.get("description")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        errors.append(f"{path}: invalid name")
    elif name != path.parent.name and not (path.parent / "kimi.plugin.json").is_file():
        errors.append(f"{path}: name must match parent directory")
    if not isinstance(description, str) or not 1 <= len(description) <= 1024:
        errors.append(f"{path}: description must contain 1 to 1024 characters")
    if fields.get("license") != "MIT":
        errors.append(f"{path}: license must be MIT")
    if len(body) >= 500:
        errors.append(f"{path}: SKILL.md body must be under 500 lines")
    if fields.get("type", "prompt") not in {"prompt", "inline"}:
        errors.append(f"{path}: type must allow automatic invocation")
    if "disableModelInvocation" in fields and (not isinstance(fields["disableModelInvocation"], bool) or fields["disableModelInvocation"]):
        errors.append(f"{path}: automatic invocation is disabled")
    return fields


def validate_markdown_links(root, errors):
    checked = 0
    for path in markdown_files(root):
        text = path.read_text(encoding="utf-8")
        if ABSOLUTE_PATH_PATTERN.search(text):
            errors.append(f"{path}: contains a machine-specific absolute path")
        for match in LINK_PATTERN.finditer(text):
            target = match.group(1).split("#", 1)[0].strip()
            if not target or re.match(r"(?:https?://|mailto:)", target):
                continue
            if target.startswith(("$", "{", "<")):
                continue
            resolved = (path.parent / target).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                errors.append(f"{path}: link leaves package root: {target}")
                continue
            if not resolved.exists():
                errors.append(f"{path}: broken link {target}")
            checked += 1
    return checked


def markdown_files(root):
    return sorted(
        path
        for path in root.rglob("*.md")
        if "dist" not in path.relative_to(root).parts and "__pycache__" not in path.parts
    )


def validate_manifest(root, errors):
    path = root / "kimi.plugin.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"{path}: {exc}")
        return {}
    if not isinstance(manifest, dict):
        errors.append("kimi.plugin.json: expected object")
        return {}
    if not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*(?:@[A-Za-z0-9_-]+)?", manifest.get("name", "")):
        errors.append("kimi.plugin.json: invalid name")
    if not isinstance(manifest.get("version"), str) or not manifest["version"]:
        errors.append("kimi.plugin.json: version required")
    if "sessionStart" in manifest:
        errors.append("kimi.plugin.json: sessionStart is not allowed for this package")
    for field in ("skills", "agents"):
        value = manifest.get(field)
        if not isinstance(value, str) or not value.startswith("./") or ".." in Path(value).parts:
            errors.append(f"kimi.plugin.json: {field} must be a safe ./ path")
            continue
        resolved = (root / value).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            errors.append(f"kimi.plugin.json: {field} escapes plugin root")
            continue
        if not resolved.is_dir():
            errors.append(f"kimi.plugin.json: {field} must be a directory")
    return manifest


def validate_portable_manifest(root, errors):
    path = root / "plugin.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"{path}: {exc}")
        return {}
    if not isinstance(manifest, dict):
        errors.append("plugin.json: expected object")
        return {}
    allowed = {"$schema", "name", "version", "description", "author", "homepage", "repository", "license", "keywords", "extensions"}
    for field in manifest:
        if field not in allowed:
            errors.append(f"plugin.json: unknown top-level field {field}")
    if manifest.get("$schema") != "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json":
        errors.append("plugin.json: unsupported Agent Plugins schema")
    name = manifest.get("name")
    if not isinstance(name, str) or not 1 <= len(name) <= 64 or not re.fullmatch(r"(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", name):
        errors.append("plugin.json: invalid Agent Plugins name")
    for field in ("version", "description", "homepage", "repository", "license"):
        if field in manifest and not isinstance(manifest[field], str):
            errors.append(f"plugin.json: {field} must be a string")
    if "keywords" in manifest and (
        not isinstance(manifest["keywords"], list)
        or any(not isinstance(value, str) for value in manifest["keywords"])
    ):
        errors.append("plugin.json: keywords must be an array of strings")
    author = manifest.get("author")
    if author is not None:
        if not isinstance(author, dict) or any(key not in {"name", "email", "url"} for key in author):
            errors.append("plugin.json: invalid author")
        elif any(not isinstance(value, str) for value in author.values()):
            errors.append("plugin.json: author fields must be strings")
    extensions = manifest.get("extensions")
    if extensions is not None and (
        not isinstance(extensions, dict)
        or any(not isinstance(value, dict) for value in extensions.values())
    ):
        errors.append("plugin.json: extensions must contain objects")
    if not (root / "skills").is_dir():
        errors.append("plugin.json: skills directory is missing")
    return manifest


def validate_claude_manifest(root, errors):
    path = root / ".claude-plugin/plugin.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"{path}: {exc}")
        return {}
    if not isinstance(manifest, dict):
        errors.append(".claude-plugin/plugin.json: expected object")
        return {}
    agents = manifest.get("agents", [])
    if not isinstance(agents, list):
        errors.append(".claude-plugin/plugin.json: agents must be an array")
        return manifest
    for agent in agents:
        if not isinstance(agent, str) or not agent.startswith("./") or ".." in Path(agent).parts:
            errors.append(".claude-plugin/plugin.json: invalid agent path")
            continue
        resolved = (root / agent).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            errors.append(".claude-plugin/plugin.json: agent path escapes plugin root")
            continue
        if not resolved.is_file():
            errors.append(f".claude-plugin/plugin.json: missing agent {agent}")
    return manifest


def parse_tool_list(value):
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    declarations = []
    buffer = []
    depth = 0
    quote = None
    for char in value:
        if quote:
            buffer.append(char)
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            buffer.append(char)
        elif char == "(":
            depth += 1
            buffer.append(char)
        elif char == ")":
            if depth == 0:
                raise ValueError("unbalanced tool declaration")
            depth -= 1
            buffer.append(char)
        elif char == "," and depth == 0:
            declarations.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(char)
    if quote or depth:
        raise ValueError("unbalanced tool declaration")
    declarations.append("".join(buffer).strip())
    return [item.strip().strip('"\'') for item in declarations if item.strip()]


def validate_reviewer_policy(path, errors, warnings):
    try:
        fields, _ = parse_frontmatter(path)
        lines = path.read_text(encoding="utf-8").splitlines()
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except (OSError, ValueError, StopIteration) as exc:
        errors.append(f"{path}: invalid agent frontmatter: {exc}")
        return
    if not fields.get("description"):
        errors.append(f"{path}: description required")
    tools = set()
    subagents = None
    in_tools = False
    for line in lines[1:end]:
        stripped = line.strip()
        if stripped.startswith("subagents:"):
            subagents = stripped.split(":", 1)[1].strip()
        if stripped.startswith("tools:"):
            value = stripped.split(":", 1)[1].strip()
            if value:
                try:
                    tools.update(parse_tool_list(value))
                except ValueError as exc:
                    errors.append(f"{path}: {exc}")
                    return
                in_tools = False
            else:
                in_tools = True
            continue
        if in_tools:
            if line.startswith("  - "):
                tools.add(line[4:].strip().strip('"\''))
            else:
                in_tools = False
    if subagents != "[]":
        errors.append(f"{path}: subagents must be an empty list in frontmatter")
    if not tools:
        errors.append(f"{path}: tools must not be empty")
    forbidden = {"Edit", "Write", "Agent", "AgentSwarm", "Task", "TaskStop", "Bash", "Skill", "CronCreate", "CronDelete", "CreateGoal", "UpdateGoal"}
    base_tools = {declaration.split("(", 1)[0].strip() for declaration in tools}
    granted = sorted(base_tools & forbidden)
    if granted:
        errors.append(f"{path}: shell, skill, write, or delegation tools are not allowed: {', '.join(granted)}")


def validate_eval_identity(case, path, seen, errors):
    case_id = case.get("id")
    if not isinstance(case_id, str) or not case_id:
        errors.append(f"{path}.id: required")
    elif case_id in seen:
        errors.append(f"{path}.id: duplicate {case_id}")
    else:
        seen.add(case_id)
    if not isinstance(case.get("query"), str) or not case["query"].strip():
        errors.append(f"{path}.query: expected non-empty string")


def validate_eval_arrays(case, path, errors):
    for field in ("expected_skills", "expected_references", "forbidden_claims"):
        value = case.get(field)
        if not isinstance(value, list):
            errors.append(f"{path}.{field}: expected array")
            continue
        if any(not isinstance(item, str) or not item.strip() for item in value):
            errors.append(f"{path}.{field}: entries must be non-empty strings")
    if not isinstance(case.get("forbidden_claims"), list) or not case["forbidden_claims"]:
        errors.append(f"{path}.forbidden_claims: at least one claim required")


def validate_eval_names(case, path, errors):
    reference_names = set(CORE_REFERENCES + BACKEND_REFERENCES + FRONTEND_REFERENCES + DOMAIN_REFERENCES)
    for field, allowed, label in (
        ("expected_skills", ALLOWED_EVAL_SKILLS, "skill"),
        ("expected_references", reference_names, "reference"),
    ):
        values = case.get(field) if isinstance(case.get(field), list) else []
        if any(not isinstance(value, str) or value not in allowed for value in values):
            errors.append(f"{path}.{field}: unknown {label}")


def validate_eval_reviewer(case, path, errors):
    expected_skills = case.get("expected_skills") if isinstance(case.get("expected_skills"), list) else []
    if "requires_independent_review" in case and not isinstance(case["requires_independent_review"], bool):
        errors.append(f"{path}.requires_independent_review: expected boolean")
    if case.get("requires_independent_review") is True and "lean-reviewer" not in expected_skills:
        errors.append(f"{path}: independent reviewer is required")
    case_id = case.get("id")
    if isinstance(case_id, str) and case_id in {"api-field-add", "self-adversarial-review"} and "lean-reviewer" not in expected_skills:
        errors.append(f"{path}: independent reviewer is required for self-review")


def validate_eval_case(case, path, seen, errors):
    if not isinstance(case, dict):
        errors.append(f"{path}: expected object")
        return
    validate_eval_identity(case, path, seen, errors)
    validate_eval_arrays(case, path, errors)
    validate_eval_names(case, path, errors)
    validate_eval_reviewer(case, path, errors)


def validate_evals(root, errors):
    path = root / "evals/tasks.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"{path}: {exc}")
        return 0
    if not isinstance(data, dict):
        errors.append("evals/tasks.json: expected object")
        return 0
    model_runs = data.get("model_runs")
    if isinstance(model_runs, bool) or not isinstance(model_runs, int) or model_runs < 0:
        errors.append("evals/tasks.json: model_runs must be a non-negative integer")
    cases = data.get("cases")
    if not isinstance(cases, list) or len(cases) < 3:
        errors.append("evals/tasks.json: at least three cases required")
        return 0
    seen = set()
    for index, case in enumerate(cases):
        path = f"evals/tasks.json.cases[{index}]"
        validate_eval_case(case, path, seen, errors)
    return len(cases)


def valid_iso_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def validate_eval_runs(root, errors):
    tasks_path = root / "evals/tasks.json"
    runs_path = root / "evals/runs.json"
    try:
        tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
        data = json.loads(runs_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"evals/runs.json: {exc}")
        return 0
    if not isinstance(tasks, dict) or not isinstance(data, dict):
        errors.append("evals/runs.json: expected task and run objects")
        return 0
    if data.get("schema_version") != "1.0":
        errors.append("evals/runs.json: unsupported schema_version")
    runs = data.get("runs")
    if not isinstance(runs, list):
        errors.append("evals/runs.json: runs must be an array")
        return 0
    seen = set()
    required_text = ("id", "model", "prompt_version", "tool_version", "case_set_version", "output_locator", "output_sha256")
    for index, run in enumerate(runs):
        path = f"evals/runs.json.runs[{index}]"
        if not isinstance(run, dict):
            errors.append(f"{path}: expected object")
            continue
        for field in required_text:
            if not isinstance(run.get(field), str) or not run[field].strip():
                errors.append(f"{path}.{field}: expected non-empty string")
        run_id = run.get("id")
        if isinstance(run_id, str) and run_id.strip():
            if run_id in seen:
                errors.append(f"{path}.id: duplicate {run_id}")
            seen.add(run_id)
        if run.get("kind") not in {"routing", "review_behavior"}:
            errors.append(f"{path}.kind: expected routing or review_behavior")
        if not valid_iso_timestamp(run.get("executed_at")):
            errors.append(f"{path}.executed_at: expected ISO timestamp with timezone")
        locator = run.get("output_locator")
        evidence_path = None
        if isinstance(locator, str) and locator.strip():
            relative = Path(locator)
            if relative.is_absolute() or ".." in relative.parts or not locator.startswith("evals/evidence/") or is_distribution_excluded(relative) or has_distribution_link(root, relative):
                errors.append(f"{path}.output_locator: expected a safe distributable evals/evidence/ path")
            else:
                evidence_path = (root / relative).resolve()
                try:
                    evidence_path.relative_to(root.resolve())
                except ValueError:
                    errors.append(f"{path}.output_locator: evidence escapes package root")
                    evidence_path = None
                if evidence_path is not None and not evidence_path.is_file():
                    errors.append(f"{path}.output_locator: evidence file is missing")
        expected_hash = run.get("output_sha256")
        if isinstance(expected_hash, str) and not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            errors.append(f"{path}.output_sha256: expected lowercase SHA-256")
        if evidence_path is not None and evidence_path.is_file() and isinstance(expected_hash, str) and re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            if file_sha256(evidence_path) != expected_hash:
                errors.append(f"{path}.output_sha256: evidence hash mismatch")
    declared = tasks.get("model_runs")
    if isinstance(declared, int) and not isinstance(declared, bool) and declared >= 0 and declared != len(runs):
        errors.append("evals/tasks.json: model_runs must match valid run records")
    return len(runs)


def validate_review_template(root, errors):
    path = root / "skills/lean-engineer/assets/backend-review-template.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        topics = set(data["project"]["required_topics"])
        if topics != TOPICS:
            errors.append("review-template.json: required topics do not match backend references")
        if data.get("release", {}).get("status") != "not_assessed":
            errors.append("review-template.json: blank template must not claim release readiness")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"{path}: {exc}")


def validate_required_files(root, errors):
    required = list(REQUIRED_FILES)
    required += [f"skills/lean-engineer/references/{name}" for name in CORE_REFERENCES]
    required += [f"skills/lean-engineer/references/backend/{name}" for name in BACKEND_REFERENCES]
    required += [f"skills/lean-engineer/references/frontend/{name}" for name in FRONTEND_REFERENCES]
    required += [f"skills/lean-engineer/references/domains/{name}" for name in DOMAIN_REFERENCES]
    for relative in required:
        if not (root / relative).is_file():
            errors.append(f"missing required file: {relative}")
    return len(required)


def validate_package_versions(root, errors):
    versions = {}
    for relative in ("SKILL.md", "skills/lean-engineer/SKILL.md"):
        path = root / relative
        if path.is_file():
            fields = validate_skill_file(path, errors)
            versions[relative] = fields.get("version")
    portable = validate_portable_manifest(root, errors)
    kimi = validate_manifest(root, errors)
    claude = validate_claude_manifest(root, errors)
    expected_version = portable.get("version")
    if any(version != expected_version for version in versions.values()):
        errors.append("skill and Agent Plugins versions do not match")
    for label, manifest in (("kimi.plugin.json", kimi), (".claude-plugin/plugin.json", claude)):
        if manifest.get("name") != portable.get("name"):
            errors.append(f"{label}: name does not match Agent Plugins")
        if manifest.get("version") != expected_version:
            errors.append(f"{label}: version does not match Agent Plugins")
        if manifest.get("license") != "MIT":
            errors.append(f"{label}: license must be MIT")


def validate_package_licenses(root, errors):
    root_license = (root / "LICENSE").read_text(encoding="utf-8")
    standalone_license = (root / "skills/lean-engineer/LICENSE").read_text(encoding="utf-8")
    if not root_license.strip() or "MIT License" not in root_license:
        errors.append("root LICENSE must contain the MIT License text")
    if root_license != standalone_license:
        errors.append("root and standalone LICENSE files differ")
    if (root / "NOTICE").read_text(encoding="utf-8") != (root / "skills/lean-engineer/NOTICE").read_text(encoding="utf-8"):
        errors.append("root and standalone NOTICE files differ")


def validate_package(root):
    errors = []
    warnings = []
    root = root.resolve()
    if not root.is_dir():
        return {"ok": False, "errors": [f"not a directory: {root}"], "warnings": [], "summary": {}}
    required_count = validate_required_files(root, errors)
    validate_package_versions(root, errors)
    links = validate_markdown_links(root, errors)
    cases = validate_evals(root, errors)
    model_runs = validate_eval_runs(root, errors)
    validate_review_template(root, errors)
    reviewer = root / "agents/lean-reviewer.md"
    if reviewer.is_file():
        validate_reviewer_policy(reviewer, errors, warnings)
    validate_package_licenses(root, errors)
    if cases and not any(case.get("expected_skills") for case in json.loads((root / "evals/tasks.json").read_text(encoding="utf-8"))["cases"]):
        warnings.append("Evaluation cases do not exercise skill activation")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "required_files": required_count,
            "markdown_files": len(markdown_files(root)),
            "local_links_checked": links,
            "evaluation_definitions": cases,
            "model_runs": model_runs,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Validate the lean-engineer distribution")
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    try:
        result = validate_package(args.root)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result = {"ok": False, "errors": [f"input: {exc}"], "warnings": [], "summary": {}}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
