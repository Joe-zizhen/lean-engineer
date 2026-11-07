import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_validators import ROOT, valid_review
from validate_review import validate_review
from validate_package import (
    validate_evals,
    validate_manifest,
    validate_package,
    validate_skill_file,
    validate_markdown_links,
)
from package import main as package_main, package_files, validate_archive, write_zip


class ReleaseGateAdversarialTests(unittest.TestCase):
    def evidence(self, item_id, kind, baseline="commit-fixture"):
        return {
            "id": item_id,
            "kind": kind,
            "locator": item_id,
            "observed_at": "2026-10-26T00:04:00Z",
            "summary": item_id,
            "baseline_ref": baseline,
        }

    def check(self, item_id, kind, status, reason=""):
        return {
            "id": item_id,
            "surface_id": "API-001",
            "topic": "api_contracts",
            "kind": kind,
            "question": item_id,
            "status": status,
            "evidence_ids": [],
            "finding_ids": [],
            "reason": reason,
        }

    def test_full_requires_all_topics(self):
        data = valid_review()
        data["project"]["mode"] = "full"
        data["project"]["required_topics"] = []
        result = validate_review(data)
        self.assertFalse(result["ok"])

    def test_placeholder_baseline_cannot_be_ready(self):
        data = valid_review()
        data["project"]["baseline"] = "unprovided"
        result = validate_review(data)
        self.assertFalse(result["ok"])

    def test_surface_location_is_required(self):
        data = valid_review()
        data["surfaces"][0]["location"] = ""
        result = validate_review(data)
        self.assertFalse(result["ok"])

    def test_normal_risk_does_not_remove_behavior_requirement(self):
        data = valid_review()
        data["surfaces"][0]["risk"] = "normal"
        data["checks"][1]["kind"] = "static"
        data["checks"][1]["evidence_ids"] = ["EV-S"]
        result = validate_review(data)
        self.assertFalse(result["ok"])

    def test_ready_requires_authority(self):
        data = valid_review()
        data["release"].pop("decision", None)
        result = validate_review(data)
        self.assertFalse(result["ok"])

    def test_release_evidence_must_link_a_passed_check(self):
        data = valid_review()
        data["evidence"].append(
            {"id": "EV-U", "kind": "test", "locator": "unrelated", "observed_at": "2026-10-26T00:04:00Z", "summary": "unrelated", "baseline_ref": "commit-fixture"}
        )
        data["release"]["evidence_ids"] = ["EV-U"]
        result = validate_review(data)
        self.assertFalse(result["ok"])

    def test_release_evidence_baseline_must_match(self):
        data = valid_review()
        data["evidence"].append(
            {"id": "EV-X", "kind": "test", "locator": "other baseline", "observed_at": "2026-10-26T00:04:00Z", "summary": "other", "baseline_ref": "other-baseline"}
        )
        data["checks"][1]["evidence_ids"].append("EV-X")
        data["release"]["evidence_ids"] = ["EV-X"]
        result = validate_review(data)
        self.assertFalse(result["ok"])

    def test_invalid_observed_at_fails(self):
        data = valid_review()
        data["evidence"][0]["observed_at"] = "not-a-time"
        result = validate_review(data)
        self.assertFalse(result["ok"])

    def test_model_only_code_finding_is_rejected(self):
        data = valid_review()
        data["evidence"].append(
            {"id": "EV-M", "kind": "model", "locator": "model.tla", "observed_at": "2026-10-26T00:04:00Z", "summary": "counterexample", "baseline_ref": "commit-fixture"}
        )
        data["findings"][0]["subject"] = "code"
        data["findings"][0]["evidence_ids"] = ["EV-M"]
        result = validate_review(data)
        self.assertFalse(result["ok"])

    def test_design_model_evidence_is_allowed(self):
        data = valid_review()
        data["evidence"].append(
            {"id": "EV-M", "kind": "model", "locator": "model.tla", "observed_at": "2026-10-26T00:04:00Z", "summary": "counterexample", "baseline_ref": "commit-fixture"}
        )
        data["findings"][0]["subject"] = "design"
        data["findings"][0]["evidence_ids"] = ["EV-M"]
        result = validate_review(data)
        self.assertTrue(result["ok"], result["errors"])

    def test_orphan_finding_is_rejected(self):
        data = valid_review()
        data["findings"][0].update({"severity": "P3", "status": "open", "confidence": "needs_validation", "surface_ids": [], "evidence_ids": []})
        data["findings"][0].pop("evidence_gap", None)
        result = validate_review(data)
        self.assertFalse(result["ok"])

    def test_malformed_required_topics_return_errors(self):
        for value in (None, [{}]):
            with self.subTest(value=value):
                data = valid_review()
                data["project"]["mode"] = "full"
                data["project"]["required_topics"] = value
                result = validate_review(data)
                self.assertFalse(result["ok"])

    def test_evidence_requires_baseline_reference(self):
        data = valid_review()
        for item in data["evidence"]:
            item.pop("baseline_ref", None)
        result = validate_review(data)
        self.assertFalse(result["ok"])

    def test_non_behavior_exemption_requires_structured_basis(self):
        for kind, reason in (("model", "TLC is not installed"), ("static", "Architecture rules are missing")):
            with self.subTest(kind=kind):
                data = valid_review()
                data["checks"].append(self.check("CHK-X", kind, "not_applicable", reason))
                result = validate_review(data)
                self.assertFalse(result["ok"], result["errors"])
                self.assertFalse(result["summary"]["release_ready"])

    def test_non_behavior_exemption_allows_current_specification(self):
        for kind in ("model", "static"):
            with self.subTest(kind=kind):
                data = valid_review()
                data["evidence"].append(self.evidence("EV-P", "specification"))
                check = self.check("CHK-X", kind, "not_applicable", "No executable object exists")
                check["reason"] = {"code": "no_check_object", "detail": "No executable object exists"}
                check["exemption"] = {"basis": "no_check_object", "scope": "No executable object exists in this scope", "capability": "available"}
                check["evidence_ids"] = ["EV-P"]
                data["checks"].append(check)
                result = validate_review(data)
                self.assertTrue(result["ok"], result["errors"])
                self.assertTrue(result["summary"]["release_ready"], result["errors"])

    def test_missing_capability_cannot_use_evidence_or_decision(self):
        for kind in ("model", "static"):
            for support in ("specification", "decision", "declared_missing_capability"):
                with self.subTest(kind=kind, support=support):
                    data = valid_review()
                    reason = "TLC is not installed" if kind == "model" else "Architecture rules are missing"
                    check = self.check("CHK-X", kind, "not_applicable", reason)
                    if support == "specification":
                        data["evidence"].append(self.evidence("EV-P", "specification"))
                        check["evidence_ids"] = ["EV-P"]
                    elif support == "decision":
                        check["decision"] = {"authority": "release owner", "reason": "Accept missing capability"}
                    else:
                        data["evidence"].append(self.evidence("EV-P", "specification"))
                        check["evidence_ids"] = ["EV-P"]
                        check["exemption"] = {"basis": "missing_capability", "scope": reason}
                    data["checks"].append(check)
                    result = validate_review(data)
                    self.assertFalse(result["ok"], result["errors"])
                    self.assertFalse(result["summary"]["release_ready"])

    def test_authorized_no_object_exemption_is_allowed(self):
        data = valid_review()
        check = self.check("CHK-X", "static", "not_applicable", "No executable object exists")
        check["reason"] = {"code": "no_check_object", "detail": "No executable object exists"}
        check["exemption"] = {"basis": "no_check_object", "scope": "No executable object exists in this scope", "capability": "available"}
        check["decision"] = {"authority": "review owner", "reason": "No executable object exists in scope"}
        data["checks"].append(check)
        result = validate_review(data)
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(result["summary"]["release_ready"], result["errors"])

    def test_placeholder_decisions_are_rejected(self):
        data = valid_review()
        data["release"]["decision"] = {"authority": "unknown", "reason": "unknown"}
        result = validate_review(data)
        self.assertFalse(result["ok"], result["errors"])
        self.assertFalse(result["summary"]["release_ready"])

        data = valid_review()
        data["findings"][0]["status"] = "accepted_risk"
        data["findings"][0]["decision"] = {"authority": "unknown", "reason": "unknown"}
        result = validate_review(data)
        self.assertFalse(result["ok"], result["errors"])

        data["findings"][0]["decision"] = {"authority": "review owner", "reason": "Accepted for this release"}
        result = validate_review(data)
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(result["summary"]["release_ready"], result["errors"])

        data = valid_review()
        check = self.check("CHK-X", "static", "not_applicable", "not needed")
        check["reason"] = {"code": "no_check_object", "detail": "not needed"}
        check["exemption"] = {"basis": "no_check_object", "scope": "unknown", "capability": "available"}
        check["decision"] = {"authority": "unknown", "reason": "unknown"}
        data["checks"].append(check)
        result = validate_review(data)
        self.assertFalse(result["ok"], result["errors"])

    def test_missing_capability_contradicts_no_check_object(self):
        for kind in ("model", "static"):
            for capability in ("available", "missing_runtime"):
                with self.subTest(kind=kind, capability=capability):
                    data = valid_review()
                    reason = "TLC is not installed" if kind == "model" else "Architecture rules are missing"
                    check = self.check("CHK-X", kind, "not_applicable", reason)
                    check["reason"] = {"code": "missing_runtime", "detail": reason}
                    check["exemption"] = {"basis": "no_check_object", "scope": reason, "capability": capability}
                    check["decision"] = {"authority": "release owner", "reason": "Accept missing capability"}
                    data["checks"].append(check)
                    result = validate_review(data)
                    self.assertFalse(result["ok"], result["errors"])
                    self.assertFalse(result["summary"]["release_ready"])

    def test_coverage_requires_valid_checks_and_schema(self):
        data = valid_review()
        data["checks"].append(self.check("CHK-X", "model", "not_applicable", "TLC is not installed"))
        result = validate_review(data)
        self.assertFalse(result["ok"], result["errors"])
        self.assertFalse(result["summary"]["review_coverage_complete"])

        data["schema_version"] = "1.1"
        result = validate_review(data)
        self.assertFalse(result["ok"], result["errors"])
        self.assertFalse(result["summary"]["review_coverage_complete"])

    def test_coverage_rejects_invalid_references_evidence_and_questions(self):
        def assert_invalid(data):
            result = validate_review(data)
            self.assertFalse(result["ok"], result["errors"])
            self.assertFalse(result["summary"]["review_coverage_complete"])

        data = valid_review()
        data["checks"][0]["evidence_ids"].append("EV-UNKNOWN")
        assert_invalid(data)

        data = valid_review()
        data["checks"][0]["finding_ids"] = ["FND-UNKNOWN"]
        assert_invalid(data)

        data = valid_review()
        data["evidence"].append(self.evidence("EV-P", "specification"))
        check = self.check("CHK-X", "static", "not_applicable", "No executable object exists")
        check["reason"] = {"code": "no_check_object", "detail": "No executable object exists"}
        check["exemption"] = {"basis": "no_check_object", "scope": "No executable object exists in this scope", "capability": "available"}
        check["evidence_ids"] = ["EV-P", "EV-UNKNOWN"]
        data["checks"].append(check)
        assert_invalid(data)

        data = valid_review()
        data["evidence"][0]["observed_at"] = "not-a-time"
        assert_invalid(data)

        data = valid_review()
        data["checks"][0]["question"] = ""
        assert_invalid(data)

    def test_malformed_fields_return_errors_instead_of_raising(self):
        def assert_invalid(data):
            result = validate_review(data)
            self.assertFalse(result["ok"], result["errors"])

        for field, value in (("surface_id", {}), ("topic", {}), ("surface_id", [])):
            with self.subTest(field=field, value=value):
                data = valid_review()
                data["checks"][0][field] = value
                assert_invalid(data)

        data = valid_review()
        data["checks"][0]["status"] = "failed"
        data["checks"][0]["finding_ids"] = ["FND-001"]
        data["findings"][0]["surface_ids"] = None
        data["release"]["status"] = "not_ready"
        assert_invalid(data)

        data = valid_review()
        data["evidence"].append(self.evidence("EV-M", "model"))
        bad_runtime = self.evidence("EV-BAD", "runtime")
        bad_runtime["kind"] = []
        data["evidence"].append(bad_runtime)
        data["checks"][1]["evidence_ids"].append("EV-BAD")
        data["findings"][0]["evidence_ids"] = ["EV-S", "EV-M", "EV-BAD"]
        assert_invalid(data)

        data = valid_review()
        bad_release = self.evidence("EV-BAD", "test")
        bad_release["kind"] = []
        data["evidence"].append(bad_release)
        data["release"]["evidence_ids"] = ["EV-BAD"]
        assert_invalid(data)

    def test_valid_failed_check_can_complete_coverage(self):
        data = valid_review()
        data["checks"][0]["status"] = "failed"
        data["checks"][0]["finding_ids"] = ["FND-001"]
        data["findings"][0]["status"] = "open"
        data["release"]["status"] = "not_ready"
        result = validate_review(data)
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(result["summary"]["review_coverage_complete"])
        self.assertFalse(result["summary"]["release_ready"])

    def test_unhashable_evidence_ids_return_errors(self):
        for invalid_id in ({}, []):
            with self.subTest(invalid_id=invalid_id):
                data = valid_review()
                data["findings"][0]["resolution_evidence_ids"] = [invalid_id]
                result = validate_review(data)
                self.assertFalse(result["ok"], result["errors"])

                data = valid_review()
                data["evidence"].append(self.evidence("EV-M", "model"))
                data["findings"][0]["evidence_ids"] = [invalid_id, "EV-M"]
                result = validate_review(data)
                self.assertFalse(result["ok"], result["errors"])

    def test_resolution_evidence_must_link_to_finding(self):
        data = valid_review()
        data["surfaces"].append({"id": "API-002", "kind": "api", "name": "other", "location": "other.py:10", "risk": "high"})
        data["evidence"].append(self.evidence("EV-OTHER", "test"))
        other = self.check("CHK-OTHER", "behavior", "passed")
        other["surface_id"] = "API-002"
        other["topic"] = "identity_access"
        other["evidence_ids"] = ["EV-OTHER"]
        data["checks"].append(other)
        data["findings"][0]["resolution_evidence_ids"] = ["EV-OTHER"]
        result = validate_review(data)
        self.assertFalse(result["ok"], result["errors"])
        self.assertTrue(any("resolved evidence must link to finding" in error for error in result["errors"]))

        data["findings"][0]["resolution_evidence_ids"] = ["EV-T"]
        result = validate_review(data)
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(result["summary"]["release_ready"], result["errors"])

    def test_model_replay_must_link_to_finding(self):
        data = valid_review()
        data["surfaces"].append({"id": "API-002", "kind": "api", "name": "other", "location": "other.py:10", "risk": "high"})
        data["evidence"].append(self.evidence("EV-M", "model"))
        data["evidence"].append(self.evidence("EV-OTHER", "runtime"))
        other = self.check("CHK-OTHER", "behavior", "passed")
        other["surface_id"] = "API-002"
        other["topic"] = "identity_access"
        other["evidence_ids"] = ["EV-OTHER"]
        data["checks"].append(other)
        data["findings"][0]["evidence_ids"] = ["EV-S", "EV-M"]
        data["findings"][0]["resolution_evidence_ids"] = ["EV-OTHER"]
        result = validate_review(data)
        self.assertFalse(result["ok"], result["errors"])
        self.assertTrue(any("model-based code finding requires related code replay" in error for error in result["errors"]))

    def test_resolved_evidence_must_match_current_baseline(self):
        data = valid_review()
        data["evidence"].append(self.evidence("EV-OLD", "test", "old-baseline"))
        data["findings"][0]["resolution_evidence_ids"] = ["EV-OLD"]
        result = validate_review(data)
        self.assertFalse(result["ok"], result["errors"])
        self.assertFalse(result["summary"]["release_ready"])
        self.assertTrue(any("resolved evidence must match current baseline" in error for error in result["errors"]))

    def test_model_resolution_requires_code_replay(self):
        data = valid_review()
        data["evidence"].append(self.evidence("EV-M", "model"))
        data["findings"][0]["resolution_evidence_ids"] = ["EV-M", "EV-S"]
        result = validate_review(data)
        self.assertFalse(result["ok"], result["errors"])
        self.assertTrue(any("model-based code finding requires related code replay" in error for error in result["errors"]))

        data["findings"][0]["evidence_ids"] = ["EV-S", "EV-M"]
        data["findings"][0]["resolution_evidence_ids"] = ["EV-T"]
        result = validate_review(data)
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(result["summary"]["release_ready"], result["errors"])


class PackageAdversarialTests(unittest.TestCase):
    def copy_package(self, destination):
        shutil.copytree(ROOT, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("dist", "__pycache__", ".pytest_cache"))
        return Path(destination)

    def test_kimi_manifest_rejects_parent_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "agents").mkdir()
            (root / "kimi.plugin.json").write_text(json.dumps({"name": "lean-engineer", "version": "1", "skills": "./../outside/", "agents": "./agents/"}), encoding="utf-8")
            errors = []
            validate_manifest(root, errors)
            self.assertTrue(errors)

    def test_kimi_manifest_rejects_files_for_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "skills").write_text("file", encoding="utf-8")
            (root / "agents").write_text("file", encoding="utf-8")
            (root / "kimi.plugin.json").write_text(json.dumps({"name": "lean-engineer", "version": "1", "skills": "./skills/", "agents": "./agents/"}), encoding="utf-8")
            errors = []
            validate_manifest(root, errors)
            self.assertTrue(errors)

    def test_reviewer_policy_cannot_be_overridden_in_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_package(directory)
            reviewer = root / "agents/lean-reviewer.md"
            text = reviewer.read_text(encoding="utf-8")
            text = text.replace("subagents: []", "subagents: [coder]")
            text = text.replace("  - Read\n", "  - Read\n  - Edit\n", 1)
            reviewer.write_text(text + "\nsubagents: []\n", encoding="utf-8")
            result = validate_package(root)
            self.assertFalse(result["ok"])

    def test_disable_model_invocation_uses_yaml_boolean(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "actual-name"
            skill.mkdir()
            path = skill / "SKILL.md"
            path.write_text('---\nname: actual-name\ndescription: "valid"\nlicense: MIT\ndisableModelInvocation: true\n---\nbody\n', encoding="utf-8")
            errors = []
            validate_skill_file(path, errors)
            self.assertTrue(any("automatic invocation is disabled" in error for error in errors))

    def test_eval_schema_is_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "evals").mkdir()
            valid = {"id": "valid", "query": "q", "expected_skills": [], "expected_references": [], "forbidden_claims": ["x"]}
            other = {"id": "other", "query": "q", "expected_skills": [], "expected_references": [], "forbidden_claims": ["x"]}
            invalid = {"id": "invalid", "query": "", "expected_skills": "", "expected_references": "", "forbidden_claims": None}
            (root / "evals/tasks.json").write_text(json.dumps({"model_runs": 0, "cases": [valid, other, invalid]}), encoding="utf-8")
            errors = []
            validate_evals(root, errors)
            self.assertTrue(errors)

    def test_self_review_cases_require_independent_reviewer(self):
        data = json.loads((ROOT / "evals/tasks.json").read_text(encoding="utf-8"))
        cases = {case["id"]: case for case in data["cases"]}
        for case_id in ("api-field-add", "self-adversarial-review"):
            self.assertIn("lean-reviewer", cases[case_id]["expected_skills"])

    def test_empty_license_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_package(directory)
            (root / "LICENSE").write_text("", encoding="utf-8")
            result = validate_package(root)
            self.assertFalse(result["ok"])

    def test_skill_archive_is_self_contained(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "skill.zip"
            count = write_zip(ROOT, output, skill_only=True)
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                self.assertEqual(count, len(names))
                self.assertEqual([name for name in names if name.endswith("/SKILL.md")], ["lean-engineer/SKILL.md"])
                self.assertTrue(all(name.startswith("lean-engineer/") for name in names))
                self.assertNotIn("plugin.json", names)
                for name in names:
                    source = ROOT / "skills" / name
                    self.assertEqual(archive.read(name), source.read_bytes(), name)
                unpacked = Path(directory) / "unpacked"
                archive.extractall(unpacked)
            errors = []
            validate_markdown_links(unpacked / "lean-engineer", errors)
            self.assertEqual(errors, [])
            self.assertEqual((unpacked / "lean-engineer/LICENSE").read_bytes(), (ROOT / "LICENSE").read_bytes())
            self.assertEqual((unpacked / "lean-engineer/NOTICE").read_bytes(), (ROOT / "NOTICE").read_bytes())

    def test_skill_archive_rejects_missing_entrypoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "package"
            (root / "skills/lean-engineer").mkdir(parents=True)
            (root / "skills/lean-engineer/LICENSE").write_text("fixture", encoding="utf-8")
            output = Path(directory) / "skill.zip"
            with self.assertRaisesRegex(ValueError, "missing"):
                write_zip(root, output, skill_only=True)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_name("skill.zip.tmp").exists())

    def test_cli_preserves_plugin_default_and_selects_skill_format(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "package"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("dist", "__pycache__"))
            version = json.loads((root / "plugin.json").read_text(encoding="utf-8"))["version"]
            for arguments, prefix, entry in (
                ([], "lean-engineer", "plugin.json"),
                (["--format", "plugin"], "lean-engineer", "plugin.json"),
                (["--format", "skill"], "lean-engineer-skill", "lean-engineer/SKILL.md"),
            ):
                with self.subTest(arguments=arguments), patch.object(sys, "argv", ["package.py", str(root)] + arguments):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        status = package_main()
                    result = json.loads(output.getvalue())
                    archive_path = root / "dist" / f"{prefix}-{version}.zip"
                    self.assertEqual(status, 0, result)
                    self.assertEqual(Path(result["summary"]["output"]), archive_path.resolve())
                    self.assertEqual(result["summary"]["sha256"], hashlib.sha256(archive_path.read_bytes()).hexdigest())
                    with zipfile.ZipFile(archive_path) as archive:
                        self.assertIn(entry, archive.namelist())

    def test_skill_archive_rejects_each_missing_license_file(self):
        for missing in ("LICENSE", "NOTICE"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "package"
                shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("dist", "__pycache__"))
                (root / "skills/lean-engineer" / missing).unlink()
                output = Path(directory) / "skill.zip"
                with self.assertRaisesRegex(ValueError, "missing"):
                    write_zip(root, output, skill_only=True)
                self.assertFalse(output.exists())
                self.assertFalse(output.with_name("skill.zip.tmp").exists())

    def test_domain_references_are_required(self):
        for name in ("mobile.md", "embedded.md", "data-engineering.md"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "package"
                shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("dist", "__pycache__"))
                relative = f"skills/lean-engineer/references/domains/{name}"
                (root / relative).unlink()
                result = validate_package(root)
                self.assertFalse(result["ok"])
                self.assertIn(f"missing required file: {relative}", result["errors"])

    def test_plugin_archive_default_and_file_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "deep/nested/plugin.zip"
            write_zip(ROOT, output)
            with zipfile.ZipFile(output) as archive:
                self.assertIn("plugin.json", archive.namelist())
                self.assertIn("skills/lean-engineer/SKILL.md", archive.namelist())
                self.assertTrue(all(info.external_attr >> 16 == 0o100644 for info in archive.infolist()))

    def test_existing_temporary_archive_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plugin.zip"
            temporary = output.with_name("plugin.zip.tmp")
            temporary.write_bytes(b"owned by another operation")
            with self.assertRaisesRegex(ValueError, "already exists"):
                write_zip(ROOT, output)
            self.assertEqual(temporary.read_bytes(), b"owned by another operation")
            self.assertFalse(output.exists())

    def test_archive_creation_error_is_not_masked_by_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plugin.zip"
            with patch("package.zipfile.ZipFile", side_effect=PermissionError("fixture access denied")):
                with self.assertRaisesRegex(PermissionError, "fixture access denied"):
                    write_zip(ROOT, output)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_name("plugin.zip.tmp").exists())

    def test_archive_rejects_unsafe_names_and_corrupt_content(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "archive.zip"
            for name in ("/absolute.txt", "../outside.txt", "dist/recursive.zip"):
                with self.subTest(name=name):
                    with zipfile.ZipFile(output, "w") as archive:
                        archive.writestr(name, "fixture")
                    with self.assertRaisesRegex(ValueError, "unsafe"):
                        validate_archive(output, ())
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("safe.txt", b"unique-payload")
            raw = output.read_bytes()
            self.assertEqual(raw.count(b"unique-payload"), 1)
            output.write_bytes(raw.replace(b"unique-payload", b"broken-payload", 1))
            with self.assertRaisesRegex(ValueError, "corrupt"):
                validate_archive(output, ())

    def test_cli_relative_output_and_failed_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "package"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("dist", "__pycache__"))
            relative = os.path.relpath(Path(directory) / "relative.zip", Path.cwd())
            cases = [([str(root), "--output", relative], True, "relative"),
                     ([str(root), "--output", str(root / "README.md")], False, "unsafe")]
            (root / "README.md").write_text("fixture", encoding="utf-8")
            for arguments, success, label in cases:
                with self.subTest(label=label), patch.object(sys, "argv", ["package.py"] + arguments):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        status = package_main()
                    result = json.loads(output.getvalue())
                    self.assertIs(result["ok"], success)
                    self.assertEqual(status, 0 if success else 1)
                    if success:
                        self.assertEqual(Path(result["summary"]["output"]), (Path(directory) / "relative.zip").resolve())
            (root / "skills/lean-engineer/references/domains/mobile.md").unlink()
            with patch.object(sys, "argv", ["package.py", str(root)]), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(package_main(), 1)
            self.assertIs(json.loads(output.getvalue())["ok"], False)

    def test_standalone_license_and_validator_command(self):
        self.assertEqual((ROOT / "LICENSE").read_text(encoding="utf-8"), (ROOT / "skills/lean-engineer/LICENSE").read_text(encoding="utf-8"))
        result = subprocess.run(
            [sys.executable, "scripts/validate_review.py", "assets/backend-review-template.json"],
            cwd=ROOT / "skills/lean-engineer",
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_package_rejects_outside_link(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            (root / "README.md").write_text("safe", encoding="utf-8")
            marker = Path(outside) / "outside-marker.txt"
            marker.write_text("outside", encoding="utf-8")
            (root / "skills").mkdir()
            link = root / "skills/linked-dir"
            if os.name == "nt":
                created = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(Path(outside))], capture_output=True)
            else:
                created = subprocess.run(["ln", "-s", str(Path(outside)), str(link)], capture_output=True)
            if created.returncode != 0:
                self.skipTest("directory link is unavailable")
            with self.assertRaises(ValueError):
                package_files(root)

    def test_package_excludes_backup_cache_and_orig(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("safe", encoding="utf-8")
            (root / "backup").mkdir()
            (root / "backup/secret.txt").write_text("secret", encoding="utf-8")
            (root / ".pytest_cache").mkdir()
            (root / ".pytest_cache/state").write_text("cache", encoding="utf-8")
            (root / "file.orig").write_text("old", encoding="utf-8")
            names = [relative.as_posix() for _, relative in package_files(root)]
            self.assertNotIn("backup/secret.txt", names)
            self.assertNotIn(".pytest_cache/state", names)
            self.assertNotIn("file.orig", names)

    def test_output_cannot_overwrite_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "README.md"
            source.write_text("safe", encoding="utf-8")
            with self.assertRaises(ValueError):
                write_zip(root, source)
            self.assertEqual(source.read_text(encoding="utf-8"), "safe")

    def test_output_cannot_be_inside_package_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("safe", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "output"):
                write_zip(root, root / "self.zip")

    def test_json_input_has_a_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.json"
            path.write_text(" " * (17 * 1024 * 1024), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "skills/lean-engineer/scripts/validate_review.py"), str(path)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("too large", result.stdout)

    def test_external_unknown_cannot_be_controlled_change(self):
        text = (ROOT / "skills/lean-engineer/references/risk-gates.md").read_text(encoding="utf-8")
        self.assertIn("外部调用方未知时不满足受控大改条件", text)

    def test_repository_location_is_not_a_risk_gate_by_itself(self):
        text = (ROOT / "skills/lean-engineer/references/risk-gates.md").read_text(encoding="utf-8")
        self.assertNotIn("对外服务的调用方不在当前仓库，或者动态引用无法列全", text)
        self.assertIn("所有受支持调用方及其版本能够逐一点名，并有实际兼容验证", text)
        self.assertIn("外部调用方未知时不满足受控大改条件", text)

    def test_verification_does_not_unconditionally_build_ci(self):
        text = (ROOT / "skills/lean-engineer/references/verification.md").read_text(encoding="utf-8")
        self.assertNotIn("5. 将同一个项目命令接入 CI。", text)
        self.assertIn("厚审大改必须实际通过", text)
        self.assertIn("缺少工具、配置或规则不得写成 `not_applicable`", text)
        self.assertIn("不得把 AI 报告写成已经建立合并阻断", text)

    def test_backend_protocol_includes_not_checked(self):
        text = (ROOT / "skills/lean-engineer/references/backend-review.md").read_text(encoding="utf-8")
        status_line = next(line for line in text.splitlines() if line.startswith("检查状态为"))
        self.assertIn("not_checked", status_line)

    def test_thick_change_requires_gates_and_independent_expectations(self):
        risk = (ROOT / "skills/lean-engineer/references/risk-gates.md").read_text(encoding="utf-8")
        verification = (ROOT / "skills/lean-engineer/references/verification.md").read_text(encoding="utf-8")
        review = (ROOT / "skills/lean-engineer/references/review-protocol.md").read_text(encoding="utf-8")
        self.assertIn("复杂度、测试与变异、架构约束三道质量门", risk)
        self.assertIn("厚审大改必须实际通过", verification)
        self.assertIn("缺少工具、配置或规则不得写成 `not_applicable`", verification)
        self.assertIn("测试期望值必须记录来源", verification)
        self.assertIn("期望值来源", review)

    def test_risk_and_delivery_status_cover_all_branches(self):
        risk = (ROOT / "skills/lean-engineer/references/risk-gates.md").read_text(encoding="utf-8")
        main = (ROOT / "skills/lean-engineer/SKILL.md").read_text(encoding="utf-8")
        verification = (ROOT / "skills/lean-engineer/references/verification.md").read_text(encoding="utf-8")
        self.assertIn("不满足受控大改的全部条件", risk)
        self.assertIn("均为 `passed` 或有依据的 `not_applicable`", main)
        self.assertIn("均为 `passed` 或有依据的 `not_applicable`", verification)

    def test_thick_eval_cases_have_complete_routing(self):
        data = json.loads((ROOT / "evals/tasks.json").read_text(encoding="utf-8"))
        cases = {case["id"]: case for case in data["cases"]}
        for case_id in ("database-migration", "payment-retry", "thick-change-quality-gates"):
            case = cases[case_id]
            required = {"risk-gates.md", "verification.md", "review-protocol.md"}
            self.assertTrue(required.issubset(set(case["expected_references"])), case_id)
            self.assertTrue(case["requires_independent_review"], case_id)
            self.assertIn("lean-reviewer", case["expected_skills"], case_id)

    def test_reviewer_tools_are_host_neutral(self):
        text = (ROOT / "agents/lean-reviewer.md").read_text(encoding="utf-8")
        metadata = text.split("---", 2)[1]
        for tool in ("Read", "Grep", "Glob", "WebSearch"):
            self.assertIn(f"  - {tool}\n", metadata)
        self.assertNotIn("FetchURL", metadata)
        self.assertNotIn("WebFetch", metadata)

    def test_quality_gate_preflight_and_ci_handoff(self):
        text = (ROOT / "skills/lean-engineer/references/verification.md").read_text(encoding="utf-8")
        self.assertIn("## 执行前检查与项目交接", text)
        self.assertIn("能力预检只描述执行条件", text)
        self.assertIn("预检通过不得写成质量门通过", text)
        self.assertIn("将同一个项目命令接入 CI", text)
        self.assertIn("不得把 AI 报告写成已经建立合并阻断", text)
        self.assertIn("不提供各项目通用的质量门执行器", text)

    def test_implementation_discipline_is_routed_and_complete(self):
        base = ROOT / "skills/lean-engineer"
        main = (base / "SKILL.md").read_text(encoding="utf-8")
        text = (base / "references/implementation.md").read_text(encoding="utf-8")
        self.assertIn("references/implementation.md", main)
        self.assertIn("纯只读 Review 不加载", main)
        required = (
            "真实调用链",
            "真实调用验证",
            "删除守卫",
            "信任边界",
            "git ls-files --eol",
            "非平凡改动",
            "默认隐藏什么",
            "如何获取全量",
            "降级返回",
            "校准参数",
            "已知上限",
        )
        for snippet in required:
            self.assertIn(snippet, text)

    def test_coding_route_declares_the_combined_baseline(self):
        text = (ROOT / "skills/lean-engineer/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("### 基础读取", text)
        self.assertIn("### 条件追加", text)
        base = text.split("### 基础读取", 1)[1].split("### 条件追加", 1)[0]
        code_route = next(line for line in base.splitlines() if line.startswith("- **代码实施**"))
        for required in ("新增", "修改", "修复", "重构", "删除", "同时读取", "references/risk-gates.md", "references/implementation.md"):
            self.assertIn(required, code_route)
        self.assertIn("经授权", base)
        self.assertIn("纯只读 Review 不加载", base)

    def test_conditional_capability_route_keeps_separate_authority(self):
        base = ROOT / "skills/lean-engineer"
        text = (base / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("### 条件追加", text)
        conditional = text.split("### 条件追加", 1)[1].split("### 领域边界与专项入口", 1)[0]
        self.assertIn("实现功能、查找复用、抽取模块或修改已收录能力", conditional)
        self.assertIn("references/reusable-capabilities.md", conditional)
        self.assertIn("累计", conditional)
        self.assertIn("读取规则不授予", conditional)
        implementation = (base / "references/implementation.md").read_text(encoding="utf-8")
        self.assertIn("../SKILL.md#基础读取", implementation)
        self.assertIn("纯只读 Review 不加载本文件", implementation)

    def test_frontend_topics_require_evidence_and_sources(self):
        base = ROOT / "skills/lean-engineer/references"
        names = (
            "routes-components.md",
            "state-data.md",
            "accessibility-layout.md",
            "security-privacy.md",
            "performance-lifecycle.md",
            "delivery-evidence.md",
        )
        for name in names:
            text = (base / "frontend" / name).read_text(encoding="utf-8")
            self.assertIn("## 最小证据与复验", text, name)
            self.assertIn("## 来源", text, name)
        protocol = (base / "frontend-review.md").read_text(encoding="utf-8")
        self.assertIn("适用对象总数", protocol)
        self.assertIn("笛卡尔积", protocol)
        self.assertIn("请求序号", (base / "frontend/state-data.md").read_text(encoding="utf-8"))
        self.assertIn("辅助技术", (base / "frontend/accessibility-layout.md").read_text(encoding="utf-8"))
        self.assertIn("真实托管环境", (base / "frontend/delivery-evidence.md").read_text(encoding="utf-8"))

    def test_runtime_files_do_not_reference_removed_external_skills(self):
        files = [ROOT / "README.md"]
        for directory in ("agents", "evals", "scripts", "skills"):
            files.extend(
                path
                for path in (ROOT / directory).rglob("*")
                if path.is_file() and path.suffix in {".md", ".json", ".py", ".yaml"}
            )
        for name in ("backend-performance-review", "statechart", "systematic-debugging"):
            for path in files:
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(name, text, f"{name} referenced by {path.relative_to(ROOT)}")

    def test_validation_record_is_current(self):
        text = (ROOT / "VALIDATION.md").read_text(encoding="utf-8")
        self.assertIn("1.0.0-beta.3", text)
        self.assertNotIn("lean-backend-review", text)
        self.assertNotIn("尚未选择开放源代码许可证", text)


class ReviewWorkflowContractTests(unittest.TestCase):
    def test_candidate_triage_and_agent_inheritance(self):
        main = (ROOT / "skills/lean-engineer/SKILL.md").read_text(encoding="utf-8")
        protocol = (ROOT / "skills/lean-engineer/references/review-protocol.md").read_text(encoding="utf-8")
        reviewer = (ROOT / "agents/lean-reviewer.md").read_text(encoding="utf-8")
        self.assertIn("任何代码 Review、修复后复审", main)
        for snippet in (
            "候选发现生成与发布前核验",
            "introduced",
            "pre_existing",
            "origin_unknown",
            "诊断和可观测性发现按实际影响判断",
            "投票只能决定候选是否进入进一步核验",
        ):
            self.assertIn(snippet, protocol)
        self.assertIn("../skills/lean-engineer/references/review-protocol.md", reviewer)
        self.assertIn("稳定 ID、来源分类", reviewer)

    def test_rereview_convergence_contract(self):
        protocol = (ROOT / "skills/lean-engineer/references/review-protocol.md").read_text(encoding="utf-8")
        for snippet in (
            "新 diff、原发现以及受影响的调用链和合同",
            "不得作为新发现重复发布",
            "保留原状态和 ID",
            "第 3 轮审查完成后",
            "达到轮次上限不等于审查通过",
            "不得为降低轮次丢弃真实发现",
        ):
            self.assertIn(snippet, protocol)

    def test_rule_feedback_contract(self):
        protocol = (ROOT / "skills/lean-engineer/references/review-protocol.md").read_text(encoding="utf-8")
        for snippet in (
            "## 规则回流",
            "项目没有审查说明时，继续依据项目现有规范、合同和本协议审查",
            "不要固定要求 `REVIEW.md`",
            "只读审查者只提出回流建议",
            "重复误报必须修正审查规则或评估依据",
            "项目没有审查说明且没有文档放置规则时",
            "未获授权时只在交接中提出建议，不创建文件",
            "`REVIEW.md` 是兜底位置，不是所有项目的强制文件名",
            "跳过路径必须列入未检查范围",
        ):
            self.assertIn(snippet, protocol)

    def test_evaluation_protocol_and_routing_cases(self):
        protocol = (ROOT / "evals/README.md").read_text(encoding="utf-8")
        for snippet in (
            "真实事故案例集和实际执行状态以",
            "干净变更案例",
            "作者修复、评论关闭或线程解决只表示处置结果",
            "defect_recall",
            "finding_precision",
            "harmful_clean_case_rate",
            "duplicate_finding_rate",
            "rounds_to_pass",
            "cap_reached_rate",
            "runs.json",
            "output_sha256",
            "expected_references` 是案例必须读取的最低集合",
            "所有指标只统计已结束运行的案例",
            "全部已结束案例数",
            "分母为零",
        ):
            self.assertIn(snippet, protocol)
        data = json.loads((ROOT / "evals/tasks.json").read_text(encoding="utf-8"))
        runs = json.loads((ROOT / "evals/runs.json").read_text(encoding="utf-8"))["runs"]
        self.assertEqual(data["model_runs"], len(runs))
        cases = {case["id"]: case for case in data["cases"]}
        for case_id in ("review-finding-triage", "rereview-convergence"):
            self.assertIn("lean-engineer", cases[case_id]["expected_skills"])
            self.assertIn("review-protocol.md", cases[case_id]["expected_references"])
        self.assertIn("达到三轮等于审查通过", cases["rereview-convergence"]["forbidden_claims"])


class AuditClosureTests(unittest.TestCase):
    def copy_with_synthetic_run(self, destination):
        root = PackageAdversarialTests().copy_package(destination)
        evidence = root / "evals/evidence/RUN-SYNTHETIC.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        payload = b'{"synthetic_fixture":true,"output":"fixture only"}\n'
        evidence.write_bytes(payload)
        run = {
            "id": "RUN-SYNTHETIC",
            "kind": "review_behavior",
            "model": "synthetic-model",
            "prompt_version": "fixture-prompt",
            "tool_version": "fixture-tool",
            "case_set_version": "fixture-cases",
            "executed_at": "2026-10-27T00:00:00Z",
            "output_locator": "evals/evidence/RUN-SYNTHETIC.json",
            "output_sha256": hashlib.sha256(payload).hexdigest(),
        }
        (root / "evals/runs.json").write_text(json.dumps({"schema_version": "1.0", "runs": [run]}, ensure_ascii=False, indent=2), encoding="utf-8")
        tasks = json.loads((root / "evals/tasks.json").read_text(encoding="utf-8"))
        tasks["model_runs"] = 1
        (root / "evals/tasks.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        return root

    def test_rereview_history_and_missing_branch(self):
        protocol = (ROOT / "skills/lean-engineer/references/review-protocol.md").read_text(encoding="utf-8")
        reviewer = (ROOT / "agents/lean-reviewer.md").read_text(encoding="utf-8")
        for snippet in (
            "上一轮完整审查报告和原发现清单",
            "原基线、当前基线、范围变化",
            "当前轮次和轮次上限",
            "没有授权记录时使用默认三轮",
            "history_consistency",
            "只有首轮审查可以为 `not_applicable`",
            "round=unknown",
            "不得自行重置轮次",
            "合并或拆分发现时保留旧 ID 映射",
        ):
            self.assertIn(snippet, protocol)
        self.assertIn("上一轮完整审查报告", reviewer)
        self.assertIn("非默认上限的授权记录", reviewer)
        self.assertIn("history_consistency", reviewer)
        cases = {case["id"]: case for case in json.loads((ROOT / "evals/tasks.json").read_text(encoding="utf-8"))["cases"]}
        for case_id in ("rereview-history-complete", "rereview-history-missing"):
            self.assertIn(case_id, cases)
            self.assertIn("review-protocol.md", cases[case_id]["expected_references"])

    def test_readonly_formal_verification_uses_delegated_output(self):
        reviewer = (ROOT / "agents/lean-reviewer.md").read_text(encoding="utf-8")
        protocol = (ROOT / "skills/lean-engineer/references/review-protocol.md").read_text(encoding="utf-8")
        self.assertIn("核对主 agent 或授权人员使用项目已有模型检查器产生的实际输出", reviewer)
        self.assertIn("formal_verification=not_run", reviewer)
        self.assertIn("不得要求只读审查者直接执行", reviewer)
        self.assertIn("只读审查者核对授权人员提供的实际输出", protocol)
        self.assertNotIn("形式化验证读取形式化验证协议，并使用项目已有的模型检查器", reviewer)

    def test_synthetic_eval_run_with_valid_evidence_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_with_synthetic_run(directory)
            result = validate_package(root)
            self.assertTrue(result["ok"], result["errors"])
            self.assertEqual(result["summary"]["model_runs"], 1)

    def test_eval_status_text_supports_future_nonzero_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_with_synthetic_run(directory)
            tasks = json.loads((root / "evals/tasks.json").read_text(encoding="utf-8"))
            protocol = (root / "evals/README.md").read_text(encoding="utf-8")
            self.assertIn("Actual model run records are stored in runs.json", tasks["note"])
            self.assertNotIn("No model behavior has been executed or passed", tasks["note"])
            self.assertIn("实际执行状态以", protocol)
            self.assertNotIn("也没有真实模型运行结果", protocol)
            result = validate_package(root)
            self.assertTrue(result["ok"], result["errors"])
            self.assertEqual(result["summary"]["model_runs"], 1)

    def test_eval_run_count_and_evidence_reject_invalid_records(self):
        for case in ("count_mismatch", "negative_count", "missing_evidence", "bad_hash", "invalid_time", "duplicate_id", "excluded_suffix", "excluded_directory"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = self.copy_with_synthetic_run(directory)
                tasks_path = root / "evals/tasks.json"
                runs_path = root / "evals/runs.json"
                if case in {"count_mismatch", "negative_count", "duplicate_id"}:
                    tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
                    tasks["model_runs"] = {"count_mismatch": 2, "negative_count": -1, "duplicate_id": 2}[case]
                    tasks_path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
                if case == "missing_evidence":
                    (root / "evals/evidence/RUN-SYNTHETIC.json").unlink()
                if case == "bad_hash":
                    (root / "evals/evidence/RUN-SYNTHETIC.json").write_bytes(b"corrupted\n")
                if case in {"invalid_time", "duplicate_id"}:
                    runs = json.loads(runs_path.read_text(encoding="utf-8"))
                    if case == "invalid_time":
                        runs["runs"][0]["executed_at"] = "yesterday"
                    else:
                        runs["runs"].append(dict(runs["runs"][0]))
                    runs_path.write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")
                if case in {"excluded_suffix", "excluded_directory"}:
                    runs = json.loads(runs_path.read_text(encoding="utf-8"))
                    old_evidence = root / "evals/evidence/RUN-SYNTHETIC.json"
                    relative = "evals/evidence/RUN-SYNTHETIC.tmp" if case == "excluded_suffix" else "evals/evidence/backup/RUN-SYNTHETIC.json"
                    evidence = root / relative
                    evidence.parent.mkdir(parents=True, exist_ok=True)
                    evidence.write_bytes(old_evidence.read_bytes())
                    old_evidence.unlink()
                    runs["runs"][0]["output_locator"] = relative
                    runs_path.write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")
                result = validate_package(root)
                self.assertFalse(result["ok"], case)
                self.assertTrue(result["errors"], case)

    def test_synthetic_eval_evidence_survives_packaging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_with_synthetic_run(directory)
            self.assertTrue(validate_package(root)["ok"])
            output = root / "dist/synthetic.zip"
            write_zip(root, output)
            with zipfile.ZipFile(output) as archive:
                self.assertIn("evals/evidence/RUN-SYNTHETIC.json", archive.namelist())
                with tempfile.TemporaryDirectory() as unpacked:
                    archive.extractall(unpacked)
                    result = validate_package(Path(unpacked))
                    self.assertTrue(result["ok"], result["errors"])
                    self.assertEqual(result["summary"]["model_runs"], 1)

    def test_eval_evidence_rejects_internal_file_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_with_synthetic_run(directory)
            evidence = root / "evals/evidence/RUN-SYNTHETIC.json"
            actual = root / "evals/evidence/actual.json"
            actual.write_bytes(evidence.read_bytes())
            evidence.unlink()
            try:
                os.symlink("actual.json", str(evidence))
            except OSError as exc:
                self.skipTest("file symbolic links are unavailable: " + str(exc))
            result = validate_package(root)
            self.assertFalse(result["ok"], result["errors"])
            with self.assertRaises(ValueError):
                write_zip(root, root / "dist/synthetic.zip")

    def test_eval_evidence_rejects_internal_directory_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_with_synthetic_run(directory)
            evidence = root / "evals/evidence/RUN-SYNTHETIC.json"
            actual = root / "evals/evidence/actual"
            actual.mkdir()
            actual.joinpath("RUN-SYNTHETIC.json").write_bytes(evidence.read_bytes())
            evidence.unlink()
            linked = root / "evals/evidence/linked"
            if os.name == "nt":
                created = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(actual)], capture_output=True)
                if created.returncode != 0:
                    try:
                        os.symlink("actual", str(linked), target_is_directory=True)
                    except OSError as exc:
                        self.skipTest("directory links are unavailable: " + str(exc))
            else:
                try:
                    os.symlink("actual", str(linked), target_is_directory=True)
                except OSError as exc:
                    self.skipTest("directory symbolic links are unavailable: " + str(exc))
            runs_path = root / "evals/runs.json"
            runs = json.loads(runs_path.read_text(encoding="utf-8"))
            runs["runs"][0]["output_locator"] = "evals/evidence/linked/RUN-SYNTHETIC.json"
            runs_path.write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")
            result = validate_package(root)
            self.assertFalse(result["ok"], result["errors"])
            with self.assertRaises(ValueError):
                write_zip(root, root / "dist/synthetic.zip")

    def test_eval_reference_matrix_covers_all_cases(self):
        cases = {case["id"]: case for case in json.loads((ROOT / "evals/tasks.json").read_text(encoding="utf-8"))["cases"]}
        required = {
            "noncoding-question": set(),
            "root-cause-only": set(),
            "button-copy": {"risk-gates.md", "implementation.md", "frontend-review.md"},
            "internal-helper": {"risk-gates.md", "implementation.md", "formal-verification.md"},
            "extra-verification": {"risk-gates.md", "implementation.md", "verification.md"},
            "api-field-add": {"risk-gates.md", "implementation.md", "verification.md", "review-protocol.md", "backend-review.md", "api-contracts.md"},
            "tenant-access": {"review-protocol.md", "backend-review.md", "identity-access.md", "api-contracts.md"},
            "database-migration": {"risk-gates.md", "implementation.md", "verification.md", "review-protocol.md", "backend-review.md", "data-consistency.md", "delivery-operations.md"},
            "payment-retry": {"risk-gates.md", "implementation.md", "verification.md", "review-protocol.md", "backend-review.md", "concurrency-messaging.md", "formal-verification.md"},
            "file-upload": {"review-protocol.md", "backend-review.md", "security-runtime.md", "identity-access.md", "api-contracts.md"},
            "performance-review": {"review-protocol.md", "backend-review.md", "delivery-operations.md"},
            "thick-change-quality-gates": {"risk-gates.md", "implementation.md", "verification.md", "review-protocol.md"},
            "quality-gate-capability-preflight": {"risk-gates.md", "implementation.md", "verification.md", "review-protocol.md"},
            "frontend-full-review": {"frontend-review.md", "routes-components.md", "state-data.md", "accessibility-layout.md", "security-privacy.md", "performance-lifecycle.md", "delivery-evidence.md", "review-protocol.md"},
            "self-adversarial-review": {"review-protocol.md", "backend-review.md", "identity-access.md", "security-diff-review.md"},
            "implementation-discipline": {"risk-gates.md", "implementation.md", "verification.md"},
            "non-git-evidence": {"risk-gates.md", "implementation.md", "verification.md"},
            "review-finding-triage": {"review-protocol.md"},
            "rereview-convergence": {"review-protocol.md"},
            "rereview-history-complete": {"review-protocol.md"},
            "rereview-history-missing": {"review-protocol.md"},
            "reuse-existing-capability": {"risk-gates.md", "implementation.md", "reusable-capabilities.md"},
            "first-capability-registration": {"risk-gates.md", "implementation.md", "reusable-capabilities.md"},
            "existing-capability-convention": {"risk-gates.md", "implementation.md", "reusable-capabilities.md"},
            "readonly-capability-audit": {"review-protocol.md", "reusable-capabilities.md"},
            "guard-validation-blocked": {"implementation.md", "verification.md"},
            "high-consequence-condition": {"risk-gates.md", "implementation.md", "verification.md", "review-protocol.md", "backend-review.md", "identity-access.md", "security-diff-review.md"},
            "additive-display-field-closed": {"risk-gates.md", "verification.md", "review-protocol.md"},
            "additive-field-unknown-client": {"risk-gates.md", "verification.md", "review-protocol.md"},
            "resource-ownership-transfer": {"implementation.md"},
            "mobile-permission-revocation": {"mobile.md", "review-protocol.md", "verification.md"},
            "device-safety-deadline": {"embedded.md", "risk-gates.md", "verification.md", "review-protocol.md"},
            "pipeline-join-grain": {"data-engineering.md", "verification.md", "review-protocol.md"},
            "security-guard-history": {"security-diff-review.md", "risk-gates.md", "verification.md", "review-protocol.md"},
            "security-transitive-worker": {"security-diff-review.md", "risk-gates.md", "verification.md", "review-protocol.md"},
            "security-effective-guard": {"security-diff-review.md", "risk-gates.md", "verification.md", "review-protocol.md"},
            "security-no-git": {"security-diff-review.md", "risk-gates.md", "verification.md", "review-protocol.md"},
            "security-dynamic-unknown": {"security-diff-review.md", "risk-gates.md", "verification.md", "review-protocol.md"},
            "security-state-dependent": {"security-diff-review.md", "risk-gates.md", "verification.md", "review-protocol.md"},
            "security-one-caller": {"security-diff-review.md", "risk-gates.md", "verification.md", "review-protocol.md"},
            "security-unrelated-many-callers": {"risk-gates.md", "review-protocol.md"},
            "security-missing-baseline": {"security-diff-review.md", "risk-gates.md", "verification.md", "review-protocol.md"},
            "security-authorized-baseline-replay": {"security-diff-review.md", "verification.md", "review-protocol.md"},
            "security-history-not-execution-permission": {"security-diff-review.md", "verification.md", "review-protocol.md"},
            "final-crossrepo-known": {"risk-gates.md", "verification.md"},
            "final-crossrepo-additive-review-pending": {"risk-gates.md", "verification.md", "review-protocol.md"},
            "final-crossrepo-unknown": {"risk-gates.md", "verification.md"},
            "final-crossrepo-latest-only": {"risk-gates.md", "verification.md"},
            "final-monorepo-unknown": {"risk-gates.md", "verification.md"},
            "final-crossrepo-high-consequence": {"risk-gates.md", "verification.md", "review-protocol.md"},
            "final-local-copy-no-ci": {"risk-gates.md", "verification.md"},
            "final-readonly-acceptance-no-ci": {"risk-gates.md", "verification.md", "review-protocol.md"},
            "final-thick-local-gates-no-ci-change": {"risk-gates.md", "verification.md", "review-protocol.md"},
            "final-ci-required-readonly": {"risk-gates.md", "verification.md"},
            "final-ci-authorized": {"risk-gates.md", "verification.md"},
            "routing-verb-aliases": {"risk-gates.md", "implementation.md"},
            "routing-fix-with-reuse": {"risk-gates.md", "implementation.md", "reusable-capabilities.md"},
            "routing-local-refactor": {"risk-gates.md", "implementation.md"},
            "routing-code-delete": {"risk-gates.md", "implementation.md"},
            "routing-dependency-integration": {"risk-gates.md", "implementation.md"},
            "routing-source-copy": {"risk-gates.md", "implementation.md", "frontend-review.md"},
            "routing-doc-only": set(),
            "routing-readonly-review": {"review-protocol.md"},
            "routing-readonly-index": {"review-protocol.md", "reusable-capabilities.md"},
            "routing-readonly-acceptance": {"risk-gates.md", "verification.md", "review-protocol.md"},
            "routing-authorized-transition": {"risk-gates.md", "implementation.md", "review-protocol.md", "reusable-capabilities.md"},
            "routing-thick-implementation": {"risk-gates.md", "implementation.md", "verification.md", "review-protocol.md"},
            "routing-first-capability": {"risk-gates.md", "implementation.md", "reusable-capabilities.md"},
            "routing-private-orchestration": {"risk-gates.md", "implementation.md", "reusable-capabilities.md"},
        }
        self.assertEqual(set(cases), set(required))
        for case_id, references in required.items():
            self.assertTrue(references.issubset(set(cases[case_id]["expected_references"])), case_id)

    def test_readonly_history_routing_does_not_require_implementation(self):
        cases = {case["id"]: case for case in json.loads((ROOT / "evals/tasks.json").read_text(encoding="utf-8"))["cases"]}
        for case_id in ("security-authorized-baseline-replay", "security-history-not-execution-permission"):
            with self.subTest(case=case_id):
                references = cases[case_id]["expected_references"]
                self.assertNotIn("implementation.md", references)
                self.assertTrue({"security-diff-review.md", "review-protocol.md", "verification.md"}.issubset(references))

    def test_performance_measurement_diagnosis_route_is_explicit(self):
        main = (ROOT / "skills/lean-engineer/SKILL.md").read_text(encoding="utf-8")
        wrapper = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        protocol = (ROOT / "evals/README.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("性能或容量的测量型诊断属于本 skill 的 Review 范围", main)
        self.assertIn("测量型诊断必须包含工作负载和可执行的测量入口", main)
        self.assertIn("其他根因未定的故障不由本 skill 主导", main)
        self.assertIn("性能或容量的测量型诊断除外", wrapper)
        self.assertIn("性能或容量的测量型诊断属于 Lean Engineer 的 Review 范围", readme)
        self.assertIn("expected_references` 是案例必须读取的最低集合", protocol)
        cases = {case["id"]: case for case in json.loads((ROOT / "evals/tasks.json").read_text(encoding="utf-8"))["cases"]}
        self.assertEqual(cases["root-cause-only"]["expected_skills"], [])
        self.assertEqual(cases["performance-review"]["expected_skills"], ["lean-engineer"])
        self.assertIn("review-protocol.md", cases["performance-review"]["expected_references"])


class MalformedStructureTests(unittest.TestCase):
    def test_null_and_object_identifiers_return_errors(self):
        data = valid_review()
        data["checks"][0]["evidence_ids"] = None
        data["checks"][1]["surface_id"] = {}
        data["checks"][1]["topic"] = {}
        data["release"]["evidence_ids"] = [{}]
        result = validate_review(data)
        self.assertFalse(result["ok"])

    def test_null_eval_lists_return_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "evals").mkdir()
            valid = {"id": "valid", "query": "q", "expected_skills": [], "expected_references": [], "forbidden_claims": ["x"]}
            other = {"id": "other", "query": "q", "expected_skills": [], "expected_references": [], "forbidden_claims": ["x"]}
            invalid = {"id": "invalid", "query": "q", "expected_skills": None, "expected_references": None, "forbidden_claims": None}
            (root / "evals/tasks.json").write_text(json.dumps({"model_runs": 0, "cases": [valid, other, invalid]}), encoding="utf-8")
            errors = []
            validate_evals(root, errors)
            self.assertTrue(errors)

    def test_manifest_root_arrays_return_errors(self):
        from validate_package import validate_claude_manifest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "kimi.plugin.json").write_text("[]", encoding="utf-8")
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin/plugin.json").write_text("[]", encoding="utf-8")
            kimi_errors = []
            claude_errors = []
            validate_manifest(root, kimi_errors)
            validate_claude_manifest(root, claude_errors)
            self.assertTrue(kimi_errors)
            self.assertTrue(claude_errors)


class SecondRoundAuditTests(unittest.TestCase):
    def test_mixed_baseline_supporting_evidence_is_rejected(self):
        data = valid_review()
        data["evidence"][1]["baseline_ref"] = "old-baseline"
        result = validate_review(data)
        self.assertFalse(result["summary"]["release_ready"])

    def test_behavior_exemption_requires_structured_basis(self):
        data = valid_review()
        data["findings"][0]["status"] = "dismissed"
        data["findings"][0]["decision"] = {"authority": "review owner", "reason": "No executable behavior exists in scope"}
        data["checks"][0]["evidence_ids"].append("EV-R")
        data["checks"][1]["status"] = "not_applicable"
        data["checks"][1]["reason"] = "not needed"
        data["checks"][1]["evidence_ids"] = []
        result = validate_review(data)
        self.assertFalse(result["summary"]["release_ready"])

        data["evidence"].append(
            {"id": "EV-P", "kind": "specification", "locator": "contract.md:10", "observed_at": "2026-10-26T00:03:00Z", "summary": "read-only exemption", "baseline_ref": "commit-fixture"}
        )
        data["checks"][1]["evidence_ids"] = ["EV-P"]
        data["checks"][1]["reason"] = {"code": "no_check_object", "detail": "The reviewed object has no executable behavior"}
        data["checks"][1]["exemption"] = {"basis": "no_check_object", "scope": "The reviewed object has no executable behavior", "capability": "available"}
        result = validate_review(data)
        self.assertTrue(result["summary"]["release_ready"], result["errors"])

    def test_reviewer_inline_tools_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = PackageAdversarialTests().copy_package(directory)
            reviewer = root / "agents/lean-reviewer.md"
            text = reviewer.read_text(encoding="utf-8")
            start = text.index("tools:\n")
            end = text.index("subagents:", start)
            text = text[:start] + "tools: [Read, Edit, Agent]\n" + text[end:]
            reviewer.write_text(text, encoding="utf-8")
            result = validate_package(root)
            self.assertFalse(result["ok"])

    def test_reviewer_task_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = PackageAdversarialTests().copy_package(directory)
            reviewer = root / "agents/lean-reviewer.md"
            reviewer.write_text(reviewer.read_text(encoding="utf-8").replace("subagents: []", "  - Task\nsubagents: []"), encoding="utf-8")
            result = validate_package(root)
            self.assertFalse(result["ok"])

    def test_reviewer_has_no_shell_or_skill_tools(self):
        text = (ROOT / "agents/lean-reviewer.md").read_text(encoding="utf-8")
        metadata = text.split("---", 2)[1]
        self.assertNotIn("Bash", metadata)
        self.assertNotIn("Skill", metadata)
        self.assertNotIn("Task", metadata)

    def test_any_error_forces_release_ready_false(self):
        data = valid_review()
        data["evidence"][0]["observed_at"] = "not-a-time"
        result = validate_review(data)
        self.assertFalse(result["ok"])
        self.assertFalse(result["summary"]["release_ready"])


class FinalAuditTests(unittest.TestCase):
    def test_parameterized_reviewer_tools_are_rejected(self):
        declarations = [
            "tools: [Read, Task(coder, explore)]\n",
            "tools:\n  - Read\n  - Task(coder)\n",
            "tools: Read, Bash(git:*)\n",
        ]
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                with tempfile.TemporaryDirectory() as directory:
                    root = PackageAdversarialTests().copy_package(directory)
                    reviewer = root / "agents/lean-reviewer.md"
                    text = reviewer.read_text(encoding="utf-8")
                    start = text.index("tools:\n")
                    end = text.index("subagents:", start)
                    reviewer.write_text(text[:start] + declaration + text[end:], encoding="utf-8")
                    result = validate_package(root)
                    self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
