import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills/lean-engineer/scripts"))

from validate_package import (
    parse_frontmatter,
    validate_evals,
    validate_manifest,
    validate_markdown_links,
    validate_package,
    validate_portable_manifest,
    validate_claude_manifest,
    validate_skill_file,
)
from validate_review import TOPICS, validate_review


def valid_review():
    return {
        "schema_version": "1.3",
        "project": {
            "name": "fixture",
            "mode": "quick",
            "baseline": "commit-fixture",
            "scope": "one endpoint",
            "access": "source_runtime",
            "required_topics": ["api_contracts", "identity_access"],
        },
        "surfaces": [
            {"id": "API-001", "kind": "api", "name": "orders", "location": "orders.py:10", "risk": "high"}
        ],
        "evidence": [
            {"id": "EV-S", "kind": "source", "locator": "orders.py:12", "observed_at": "2026-10-26T00:00:00Z", "summary": "source checked", "baseline_ref": "commit-fixture"},
            {"id": "EV-T", "kind": "test", "locator": "pytest orders", "observed_at": "2026-10-26T00:01:00Z", "summary": "passed", "baseline_ref": "commit-fixture"},
            {"id": "EV-R", "kind": "runtime", "locator": "POST /test/orders", "observed_at": "2026-10-26T00:02:00Z", "summary": "observed", "baseline_ref": "commit-fixture"},
        ],
        "checks": [
            {
                "id": "CHK-001",
                "surface_id": "API-001",
                "topic": "api_contracts",
                "kind": "static",
                "question": "Is the contract explicit?",
                "status": "passed",
                "evidence_ids": ["EV-S"],
                "finding_ids": [],
                "reason": "",
            },
            {
                "id": "CHK-002",
                "surface_id": "API-001",
                "topic": "identity_access",
                "kind": "behavior",
                "question": "Is cross-tenant access denied?",
                "status": "passed",
                "evidence_ids": ["EV-T", "EV-R"],
                "finding_ids": ["FND-001"],
                "reason": "",
            },
        ],
        "findings": [
            {
                "id": "FND-001",
                "title": "fixed contract issue",
                "severity": "P1",
                "confidence": "confirmed",
                "status": "resolved",
                "subject": "code",
                "actual": "old behavior",
                "expected": "required behavior",
                "impact": "contract failure",
                "evidence_ids": ["EV-S"],
                "reproduction": "request fixture",
                "remediation": "use the contract",
                "retest": "pytest orders",
                "owner": "backend",
                "root_cause": "missing check",
                "root_cause_confidence": "confirmed",
                "surface_ids": ["API-001"],
                "resolution_evidence_ids": ["EV-T"],
            }
        ],
        "release": {"status": "ready", "reason": "fixture passes", "evidence_ids": ["EV-R"], "decision": {"authority": "release owner", "reason": "verified fixture"}},
    }


class ReviewValidatorTests(unittest.TestCase):
    def test_template_is_valid_but_not_ready(self):
        data = json.loads((ROOT / "skills/lean-engineer/assets/backend-review-template.json").read_text(encoding="utf-8"))
        result = validate_review(data)
        self.assertTrue(result["ok"], result["errors"])
        self.assertFalse(result["summary"]["release_ready"])

    def test_valid_ready_review(self):
        result = validate_review(valid_review())
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(result["summary"]["review_coverage_complete"])
        self.assertTrue(result["summary"]["release_ready"])

    def test_pass_requires_evidence(self):
        data = valid_review()
        data["checks"][0]["evidence_ids"] = []
        result = validate_review(data)
        self.assertFalse(result["ok"])
        self.assertTrue(any("passed requires evidence" in error for error in result["errors"]))

    def test_behavior_rejects_source_only_evidence(self):
        data = valid_review()
        data["checks"][1]["evidence_ids"] = ["EV-S"]
        result = validate_review(data)
        self.assertFalse(result["ok"])
        self.assertTrue(any("does not support behavior" in error for error in result["errors"]))

    def test_failed_check_requires_finding(self):
        data = valid_review()
        data["checks"][1]["status"] = "failed"
        data["checks"][1]["finding_ids"] = []
        result = validate_review(data)
        self.assertFalse(result["ok"])
        self.assertTrue(any("failed requires a finding" in error for error in result["errors"]))

    def test_resolved_finding_requires_resolution_evidence(self):
        data = valid_review()
        data["findings"][0]["resolution_evidence_ids"] = []
        result = validate_review(data)
        self.assertFalse(result["ok"])
        self.assertTrue(any("resolved requires resolution evidence" in error for error in result["errors"]))

    def test_accepted_risk_requires_human_decision(self):
        data = valid_review()
        data["findings"][0]["status"] = "accepted_risk"
        result = validate_review(data)
        self.assertFalse(result["ok"])
        self.assertTrue(any("authority and reason required" in error for error in result["errors"]))

    def test_ready_rejects_blocked_check(self):
        data = valid_review()
        data["checks"][1]["status"] = "blocked"
        data["checks"][1]["reason"] = "runtime unavailable"
        result = validate_review(data)
        self.assertFalse(result["ok"])
        self.assertFalse(result["summary"]["release_ready"])

    def test_full_requires_all_declared_topics(self):
        data = valid_review()
        data["project"]["mode"] = "full"
        result = validate_review(data)
        self.assertFalse(result["ok"])
        self.assertFalse(result["summary"]["review_coverage_complete"])

    def test_unknown_evidence_reference_fails(self):
        data = valid_review()
        data["checks"][0]["evidence_ids"] = ["EV-UNKNOWN"]
        result = validate_review(data)
        self.assertFalse(result["ok"])
        self.assertTrue(any("unknown reference" in error for error in result["errors"]))

    def test_not_assessed_does_not_pass_require_ready(self):
        data = valid_review()
        data["release"]["status"] = "not_assessed"
        result = validate_review(data)
        self.assertTrue(result["ok"], result["errors"])
        self.assertFalse(result["summary"]["release_ready"])

    def test_specification_cannot_confirm_or_resolve(self):
        data = valid_review()
        data["evidence"].append(
            {"id": "EV-P", "kind": "specification", "locator": "contract.md", "observed_at": "2026-10-26T00:03:00Z", "summary": "expected only"}
        )
        data["findings"][0]["evidence_ids"] = ["EV-P"]
        data["findings"][0]["resolution_evidence_ids"] = ["EV-P"]
        result = validate_review(data)
        self.assertFalse(result["ok"])
        self.assertTrue(any("confirmed requires evidence" in error for error in result["errors"]))
        self.assertTrue(any("resolved requires resolution evidence" in error for error in result["errors"]))

    def test_finding_text_cannot_be_empty(self):
        data = valid_review()
        data["findings"][0]["actual"] = ""
        result = validate_review(data)
        self.assertFalse(result["ok"])
        self.assertTrue(any(".actual: expected non-empty string" in error for error in result["errors"]))

    def test_high_risk_surface_requires_behavior_evidence(self):
        data = valid_review()
        data["checks"][1]["kind"] = "static"
        data["checks"][1]["evidence_ids"] = ["EV-S"]
        result = validate_review(data)
        self.assertFalse(result["ok"])
        self.assertFalse(result["summary"]["release_ready"])


class PackageValidatorTests(unittest.TestCase):
    def test_eval_input_boundaries(self):
        import copy
        base = {"model_runs": 0, "cases": [
            {"id": f"case-{index}", "query": "q", "expected_skills": ["lean-engineer"],
             "expected_references": ["risk-gates.md"], "forbidden_claims": ["unverified claim"]}
            for index in range(3)
        ]}
        invalid = [None, [], {"model_runs": 0, "cases": []}, {"model_runs": 0, "cases": {}}]
        for value in (True, None, -1, "0"):
            data = copy.deepcopy(base)
            data["model_runs"] = value
            invalid.append(data)
        for field, values in {
            "id": ("", None, 7), "query": (" ", None, 7),
            "expected_skills": (["unknown-skill"], [""], [7], None),
            "expected_references": (["unknown.md"], [""], [7], None),
            "forbidden_claims": ([], [" "], [7], None),
            "requires_independent_review": ("true", 1, True),
        }.items():
            for value in values:
                data = copy.deepcopy(base)
                data["cases"][0][field] = value
                invalid.append(data)
        for value in (None, [], "case"):
            data = copy.deepcopy(base)
            data["cases"][0] = value
            invalid.append(data)
        for case_id in ("api-field-add", "self-adversarial-review"):
            data = copy.deepcopy(base)
            data["cases"][0]["id"] = case_id
            invalid.append(data)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "evals").mkdir()
            for value, expected_valid in [(base, True)] + [(item, False) for item in invalid]:
                with self.subTest(value=value):
                    (root / "evals/tasks.json").write_text(json.dumps(value), encoding="utf-8")
                    errors = []
                    validate_evals(root, errors)
                    self.assertEqual(not errors, expected_valid, errors)

    def test_package_missing_root_and_optional_checks_do_not_raise(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "package"
            result = validate_package(root)
            self.assertIs(result["ok"], False)
            self.assertTrue(result["errors"])
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("dist", "__pycache__"))
            (root / "SKILL.md").unlink()
            (root / "agents/lean-reviewer.md").unlink()
            result = validate_package(root)
            self.assertIs(result["ok"], False)
            self.assertIn("missing required file: SKILL.md", result["errors"])
            self.assertNotIn("skill and Agent Plugins versions do not match", result["errors"])
            self.assertFalse(any("invalid agent frontmatter" in error for error in result["errors"]))

    def test_manifest_consistency_in_both_directions(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "package"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("dist", "__pycache__"))
            for relative in ("kimi.plugin.json", ".claude-plugin/plugin.json"):
                path = root / relative
                original = path.read_text(encoding="utf-8")
                for field, values, message in (
                    ("name", ("aaa", "zzz"), "name does not match Agent Plugins"),
                    ("version", ("0.0.1", "9.0.0"), "version does not match Agent Plugins"),
                    ("license", ("Apache-2.0", "ZZZ"), "license must be MIT"),
                ):
                    for value in values:
                        with self.subTest(relative=relative, field=field, value=value):
                            manifest = json.loads(original)
                            manifest[field] = value
                            path.write_text(json.dumps(manifest), encoding="utf-8")
                            result = validate_package(root)
                            self.assertIn(f"{relative}: {message}", result["errors"])
                path.write_text(original, encoding="utf-8")
            path = root / "SKILL.md"
            original = path.read_text(encoding="utf-8")
            current = json.loads((root / "plugin.json").read_text(encoding="utf-8"))["version"]
            for version in ("0.0.1", "9.0.0"):
                path.write_text(original.replace(f'version: "{current}"', f'version: "{version}"'), encoding="utf-8")
                self.assertIn("skill and Agent Plugins versions do not match", validate_package(root)["errors"])

    def test_license_content_and_distribution_copies(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "package"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("dist", "__pycache__"))
            for relative, message in (("LICENSE", "root and standalone LICENSE files differ"), ("NOTICE", "root and standalone NOTICE files differ")):
                path = root / relative
                original = path.read_text(encoding="utf-8")
                for value in (" " + original, "~" + original):
                    path.write_text(value, encoding="utf-8")
                    self.assertIn(message, validate_package(root)["errors"])
                path.write_text(original, encoding="utf-8")
            (root / "LICENSE").write_text("not a license", encoding="utf-8")
            self.assertIn("root LICENSE must contain the MIT License text", validate_package(root)["errors"])

    def test_evaluation_activation_warning(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "package"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("dist", "__pycache__"))
            self.assertEqual(validate_package(root)["warnings"], [])
            path = root / "evals/tasks.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            for case in data["cases"]:
                case["expected_skills"] = []
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertIn("Evaluation cases do not exercise skill activation", validate_package(root)["warnings"])
            data["cases"] = []
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(validate_package(root)["warnings"], [])

    def test_current_package_is_valid(self):
        result = validate_package(ROOT)
        self.assertTrue(result["ok"], result["errors"])
        runs = json.loads((ROOT / "evals/runs.json").read_text(encoding="utf-8"))["runs"]
        self.assertEqual(result["summary"]["model_runs"], len(runs))

    def test_missing_capability_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            import shutil
            root = Path(directory) / "package"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("dist", "__pycache__"))
            (root / "skills/lean-engineer/references/reusable-capabilities.md").unlink()
            result = validate_package(root)
            self.assertFalse(result["ok"])
            self.assertTrue(any("reusable-capabilities.md" in error for error in result["errors"]))

    def test_security_reference_is_required_without_inbound_links(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "package"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("dist", "__pycache__"))
            relative = "skills/lean-engineer/references/security-diff-review.md"
            for path in root.rglob("*.md"):
                text = path.read_text(encoding="utf-8")
                if "security-diff-review.md" in text:
                    with path.open("w", encoding="utf-8", newline="\n") as stream:
                        stream.write(text.replace("security-diff-review.md", "review-protocol.md"))
            (root / relative).unlink()
            result = validate_package(root)
            self.assertFalse(result["ok"])
            self.assertIn(f"missing required file: {relative}", result["errors"])

    def test_frontmatter_reads_metadata_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "SKILL.md"
            path.write_text('---\nname: fixture\ndescription: "fixture"\nmetadata:\n  version: "9.8.7"\n---\nbody\n', encoding="utf-8")
            fields, _ = parse_frontmatter(path)
            self.assertEqual(fields["version"], "9.8.7")

    def test_skill_name_must_match_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "actual-name"
            skill.mkdir()
            path = skill / "SKILL.md"
            path.write_text('---\nname: other-name\ndescription: "valid description"\n---\nbody\n', encoding="utf-8")
            errors = []
            validate_skill_file(path, errors)
            self.assertTrue(any("must match parent" in error for error in errors))

    def test_eval_ids_must_be_unique(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "evals").mkdir()
            case = {"id": "same", "query": "q", "expected_skills": [], "expected_references": [], "forbidden_claims": []}
            (root / "evals/tasks.json").write_text(json.dumps({"model_runs": 0, "cases": [case, case, case]}), encoding="utf-8")
            errors = []
            validate_evals(root, errors)
            self.assertTrue(any("duplicate same" in error for error in errors))

    def test_markdown_link_must_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SKILL.md").write_text("[missing](references/missing.md)", encoding="utf-8")
            errors = []
            validate_markdown_links(root, errors)
            self.assertTrue(any("broken link" in error for error in errors))

    def test_manifest_rejects_session_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "kimi.plugin.json").write_text(
                json.dumps({"name": "lean-engineer", "version": "1", "skills": "./skills/", "agents": "./agents/", "sessionStart": {"skill": "lean-engineer"}}),
                encoding="utf-8",
            )
            errors = []
            validate_manifest(root, errors)
            self.assertTrue(any("sessionStart" in error for error in errors))

    def test_portable_manifest_rejects_unknown_top_level_field(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "plugin.json").write_text(
                json.dumps({"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "lean-engineer", "skills": []}),
                encoding="utf-8",
            )
            errors = []
            validate_portable_manifest(root, errors)
            self.assertTrue(any("unknown top-level field skills" in error for error in errors))

    def test_portable_manifest_requires_canonical_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "plugin.json").write_text(json.dumps({"$schema": "wrong", "name": "lean-engineer"}), encoding="utf-8")
            errors = []
            validate_portable_manifest(root, errors)
            self.assertTrue(any("unsupported Agent Plugins schema" in error for error in errors))

    def test_skill_license_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "actual-name"
            skill.mkdir()
            path = skill / "SKILL.md"
            path.write_text('---\nname: actual-name\ndescription: "valid description"\n---\nbody\n', encoding="utf-8")
            errors = []
            validate_skill_file(path, errors)
            self.assertTrue(any("license must be MIT" in error for error in errors))

    def test_claude_manifest_checks_agent_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin/plugin.json").write_text(json.dumps({"agents": ["./agents/missing.md"]}), encoding="utf-8")
            errors = []
            validate_claude_manifest(root, errors)
            self.assertTrue(any("missing agent" in error for error in errors))


class MalformedInputTests(unittest.TestCase):
    def test_invalid_project_returns_errors(self):
        data = copy.deepcopy(valid_review())
        data["project"] = []
        result = validate_review(data)
        self.assertFalse(result["ok"])
        self.assertTrue(any("expected object" in error for error in result["errors"]))

    def test_invalid_reference_type_returns_errors(self):
        data = copy.deepcopy(valid_review())
        data["checks"][0]["evidence_ids"] = [{}]
        result = validate_review(data)
        self.assertFalse(result["ok"])
        self.assertTrue(any("unknown reference" in error for error in result["errors"]))

    def test_non_string_eval_id_returns_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "evals").mkdir()
            valid = {"id": "valid", "query": "q", "expected_skills": [], "expected_references": [], "forbidden_claims": []}
            other = {"id": "other", "query": "q", "expected_skills": [], "expected_references": [], "forbidden_claims": []}
            invalid = {"id": [], "query": "q", "expected_skills": [], "expected_references": [], "forbidden_claims": []}
            (root / "evals/tasks.json").write_text(json.dumps({"model_runs": 0, "cases": [valid, other, invalid]}), encoding="utf-8")
            errors = []
            validate_evals(root, errors)
            self.assertTrue(any("id: required" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
