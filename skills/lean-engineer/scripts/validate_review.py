#!/usr/bin/env python3
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

TOPICS = {
    "api_contracts",
    "identity_access",
    "data_consistency",
    "concurrency_messaging",
    "security_runtime",
    "delivery_operations",
}
CHECK_STATUSES = ("passed", "failed", "blocked", "not_run", "not_checked", "not_applicable")
CHECK_KINDS = ("static", "behavior", "measurement", "model")
EVIDENCE_KINDS = ("source", "test", "runtime", "measurement", "specification", "model")
FINDING_STATUSES = ("open", "in_progress", "fixed_unverified", "resolved", "accepted_risk", "dismissed")
CONFIDENCE = ("confirmed", "probable", "needs_validation")
SEVERITIES = ("P0", "P1", "P2", "P3")
ACCESS_MODES = ("source_only", "source_runtime", "runtime_only", "unknown")
PLACEHOLDERS = {"", "unprovided", "unknown", "unassessed", "n/a", "na", "tbd", "todo", "template", "not provided", "not needed"}
MAX_REVIEW_BYTES = 16 * 1024 * 1024
EVIDENCE_FOR_CHECK = {
    "static": ("source", "test", "runtime"),
    "behavior": ("test", "runtime"),
    "measurement": ("measurement",),
    "model": ("model", "test"),
}


def is_text(value):
    return isinstance(value, str) and bool(value.strip())


def is_placeholder(value):
    if not isinstance(value, str):
        return True
    normalized = value.strip().lower()
    return normalized in PLACEHOLDERS or normalized.startswith("template only") or normalized.startswith("unavailable")


def is_timestamp(value):
    if not is_text(value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def require_fields(value, fields, path, errors):
    if not isinstance(value, dict):
        errors.append(f"{path}: expected object")
        return False
    for field in fields:
        if field not in value:
            errors.append(f"{path}.{field}: missing")
    return True


def validate_ids(items, name, required, errors):
    result = {}
    if not isinstance(items, list):
        errors.append(f"{name}: expected array")
        return result
    for index, item in enumerate(items):
        path = f"{name}[{index}]"
        if not require_fields(item, required, path, errors):
            continue
        item_id = item.get("id")
        if not is_text(item_id):
            errors.append(f"{path}.id: expected non-empty string")
        elif item_id in result:
            errors.append(f"{path}.id: duplicate {item_id}")
        else:
            result[item_id] = item
    return result


def validate_references(values, targets, path, errors):
    if not isinstance(values, list):
        errors.append(f"{path}: expected array")
        return
    for value in values:
        if not is_text(value) or value not in targets:
            errors.append(f"{path}: unknown reference {value}")


def has_evidence_kind(holder, evidence, allowed):
    evidence_ids = holder.get("evidence_ids", [])
    if not isinstance(evidence_ids, list):
        return False
    return any(
        is_text(item_id)
        and item_id in evidence
        and is_text(evidence[item_id].get("kind"))
        and evidence[item_id]["kind"] in allowed
        for item_id in evidence_ids
    )


def has_authorized_decision(decision):
    if not isinstance(decision, dict):
        return False
    authority = decision.get("authority")
    reason = decision.get("reason")
    return is_text(authority) and not is_placeholder(authority) and is_text(reason) and not is_placeholder(reason)


def has_valid_exemption(check, evidence, baseline):
    exemption = check.get("exemption")
    if not isinstance(exemption, dict) or exemption.get("basis") != "no_check_object":
        return False
    scope = exemption.get("scope")
    if not is_text(scope) or is_placeholder(scope) or exemption.get("capability") != "available":
        return False
    reason = check.get("reason")
    if not isinstance(reason, dict) or reason.get("code") != "no_check_object":
        return False
    detail = reason.get("detail")
    if not is_text(detail) or is_placeholder(detail):
        return False
    if has_authorized_decision(check.get("decision")):
        return True
    evidence_ids = check.get("evidence_ids", [])
    if not isinstance(evidence_ids, list):
        return False
    return any(
        is_text(item_id)
        and item_id in evidence
        and evidence[item_id].get("kind") == "specification"
        and evidence[item_id].get("baseline_ref") == baseline
        for item_id in evidence_ids
    )


def related_check_evidence_ids(finding, checks):
    finding_id = finding.get("id")
    surface_ids = finding.get("surface_ids")
    if not is_text(finding_id) or not isinstance(surface_ids, list):
        return set()
    related_ids = set()
    for check in checks.values():
        if check.get("status") != "passed" or check.get("surface_id") not in surface_ids:
            continue
        finding_ids = check.get("finding_ids")
        if not isinstance(finding_ids, list) or finding_id not in finding_ids:
            continue
        evidence_ids = check.get("evidence_ids")
        if isinstance(evidence_ids, list):
            related_ids.update(item_id for item_id in evidence_ids if is_text(item_id))
    return related_ids


def has_related_evidence(finding, checks, evidence, allowed, required_ids):
    related_ids = related_check_evidence_ids(finding, checks).intersection(required_ids)
    return any(
        item_id in evidence and is_text(evidence[item_id].get("kind")) and evidence[item_id]["kind"] in allowed
        for item_id in related_ids
    )


def validate_project(project, errors):
    if not require_fields(project, ["name", "mode", "baseline", "scope", "access", "required_topics"], "$.project", errors):
        return None, None
    for field in ("name", "baseline", "scope", "access"):
        if not is_text(project.get(field)):
            errors.append(f"$.project.{field}: expected non-empty string")
    if project.get("access") not in ACCESS_MODES:
        errors.append("$.project.access: invalid")
    mode = project.get("mode")
    if mode not in ("quick", "full"):
        errors.append("$.project.mode: expected quick or full")
    required_topics = project.get("required_topics")
    if not isinstance(required_topics, list):
        errors.append("$.project.required_topics: expected array")
        required_topics = []
    elif any(not is_text(topic) or topic not in TOPICS for topic in required_topics):
        errors.append("$.project.required_topics: contains invalid topic")
        required_topics = [topic for topic in required_topics if is_text(topic)]
    return mode, required_topics


def validate_surfaces(data, errors):
    surfaces = validate_ids(data, "$.surfaces", ["id", "kind", "name", "location", "risk"], errors)
    for path, surface in surfaces.items():
        for field in ("name", "location"):
            if not is_text(surface.get(field)):
                errors.append(f"$.surfaces[{path}].{field}: expected non-empty string")
        if surface.get("kind") not in ("api", "event", "job", "migration", "datastore", "external_service", "configuration", "other"):
            errors.append(f"$.surfaces[{path}].kind: invalid")
        if surface.get("risk") not in ("high", "normal"):
            errors.append(f"$.surfaces[{path}].risk: expected high or normal")
    return surfaces


def validate_evidence(data, errors):
    evidence = validate_ids(data, "$.evidence", ["id", "kind", "locator", "observed_at", "summary", "baseline_ref"], errors)
    for path, item in evidence.items():
        if item.get("kind") not in EVIDENCE_KINDS:
            errors.append(f"$.evidence[{path}].kind: invalid")
        for field in ("locator", "summary", "baseline_ref"):
            if not is_text(item.get(field)):
                errors.append(f"$.evidence[{path}].{field}: expected non-empty string")
        if not is_timestamp(item.get("observed_at")):
            errors.append(f"$.evidence[{path}].observed_at: expected ISO 8601 timestamp with timezone")
    return evidence


def validate_checks(data, surfaces, evidence, baseline, errors):
    checks = validate_ids(
        data,
        "$.checks",
        ["id", "surface_id", "topic", "kind", "question", "status", "evidence_ids", "finding_ids", "reason"],
        errors,
    )
    for path, check in checks.items():
        if not is_text(check.get("question")):
            errors.append(f"$.checks[{path}].question: expected non-empty string")
        if not is_text(check.get("surface_id")) or check.get("surface_id") not in surfaces:
            errors.append(f"$.checks[{path}].surface_id: unknown reference")
        if not is_text(check.get("topic")) or check.get("topic") not in TOPICS:
            errors.append(f"$.checks[{path}].topic: invalid")
        kind = check.get("kind")
        if kind not in CHECK_KINDS:
            errors.append(f"$.checks[{path}].kind: invalid")
        status = check.get("status")
        if status not in CHECK_STATUSES:
            errors.append(f"$.checks[{path}].status: invalid")
        validate_references(check.get("evidence_ids"), evidence, f"$.checks[{path}].evidence_ids", errors)
        if status in ("passed", "failed"):
            if not check.get("evidence_ids"):
                errors.append(f"$.checks[{path}]: {status} requires evidence")
            elif is_text(kind) and kind in EVIDENCE_FOR_CHECK and not has_evidence_kind(check, evidence, EVIDENCE_FOR_CHECK[kind]):
                errors.append(f"$.checks[{path}]: evidence kind does not support {kind}")
        if status == "failed" and not check.get("finding_ids"):
            errors.append(f"$.checks[{path}]: failed requires a finding")
        if status in ("blocked", "not_run", "not_checked") and not is_text(check.get("reason")):
            errors.append(f"$.checks[{path}].reason: required for {status}")
        if status == "not_applicable" and not has_valid_exemption(check, evidence, baseline):
            errors.append(f"$.checks[{path}]: not_applicable requires specification evidence or an authorized decision")
    return checks


def validate_findings(data, surfaces, evidence, checks, baseline, errors):
    findings = validate_ids(
        data,
        "$.findings",
        [
            "id", "title", "severity", "confidence", "status", "subject", "actual", "expected", "impact",
            "evidence_ids", "reproduction", "remediation", "retest", "owner", "root_cause",
            "root_cause_confidence", "surface_ids", "resolution_evidence_ids",
        ],
        errors,
    )
    for path, finding in findings.items():
        for field in ("title", "actual", "expected", "impact", "reproduction", "remediation", "retest", "owner", "root_cause"):
            if not is_text(finding.get(field)):
                errors.append(f"$.findings[{path}].{field}: expected non-empty string")
        if finding.get("severity") not in SEVERITIES:
            errors.append(f"$.findings[{path}].severity: invalid")
        confidence = finding.get("confidence")
        if confidence not in CONFIDENCE:
            errors.append(f"$.findings[{path}].confidence: invalid")
        if finding.get("root_cause_confidence") not in CONFIDENCE:
            errors.append(f"$.findings[{path}].root_cause_confidence: invalid")
        subject = finding.get("subject")
        if subject not in ("code", "design"):
            errors.append(f"$.findings[{path}].subject: expected code or design")
        status = finding.get("status")
        if status not in FINDING_STATUSES:
            errors.append(f"$.findings[{path}].status: invalid")
        if not finding.get("surface_ids"):
            errors.append(f"$.findings[{path}].surface_ids: at least one surface required")
        validate_references(finding.get("surface_ids"), surfaces, f"$.findings[{path}].surface_ids", errors)
        validate_references(finding.get("evidence_ids"), evidence, f"$.findings[{path}].evidence_ids", errors)
        validate_references(finding.get("resolution_evidence_ids"), evidence, f"$.findings[{path}].resolution_evidence_ids", errors)
        if not finding.get("evidence_ids") and not is_text(finding.get("evidence_gap")):
            errors.append(f"$.findings[{path}]: evidence or evidence_gap required")
        if "evidence_gap" in finding and not is_text(finding["evidence_gap"]):
            errors.append(f"$.findings[{path}].evidence_gap: expected non-empty string")
        if confidence == "confirmed":
            allowed = {"source", "test", "runtime", "measurement"}
            if subject == "design":
                allowed = allowed | {"model"}
            if not has_evidence_kind(finding, evidence, allowed):
                errors.append(f"$.findings[{path}]: confirmed requires evidence")
        if confidence == "probable" and not has_evidence_kind(finding, evidence, {"source", "test", "runtime", "measurement", "model"}):
            errors.append(f"$.findings[{path}]: probable requires supporting evidence")
        original_ids = finding.get("evidence_ids")
        resolution_ids = finding.get("resolution_evidence_ids")
        combined_ids = []
        if isinstance(original_ids, list):
            combined_ids.extend(item_id for item_id in original_ids if is_text(item_id))
        if isinstance(resolution_ids, list):
            combined_ids.extend(item_id for item_id in resolution_ids if is_text(item_id))
        combined_check = {"evidence_ids": combined_ids}
        if subject == "code" and has_evidence_kind(combined_check, evidence, {"model"}) and not has_related_evidence(finding, checks, evidence, {"test", "runtime"}, combined_ids):
            errors.append(f"$.findings[{path}]: model-based code finding requires related code replay")
        resolution_check = {"evidence_ids": resolution_ids}
        allowed_resolution = {"source", "test", "runtime", "measurement"}
        if subject == "design":
            allowed_resolution = allowed_resolution | {"model"}
        related_ids = related_check_evidence_ids(finding, checks)
        if status == "resolved":
            if not has_evidence_kind(resolution_check, evidence, allowed_resolution):
                errors.append(f"$.findings[{path}]: resolved requires resolution evidence")
            if not isinstance(resolution_ids, list) or any(not is_text(item_id) or item_id not in related_ids for item_id in resolution_ids):
                errors.append(f"$.findings[{path}]: resolved evidence must link to finding through a passed check")
            if not isinstance(resolution_ids, list) or any(
                not is_text(item_id) or item_id not in evidence or evidence[item_id].get("baseline_ref") != baseline
                for item_id in resolution_ids
            ):
                errors.append(f"$.findings[{path}]: resolved evidence must match current baseline")
        if status in ("accepted_risk", "dismissed"):
            decision = finding.get("decision")
            if not has_authorized_decision(decision):
                errors.append(f"$.findings[{path}].decision: authority and reason required")
    return findings


def has_valid_failed_finding(check, findings):
    finding_ids = check.get("finding_ids")
    if not isinstance(finding_ids, list) or not finding_ids:
        return False
    for finding_id in finding_ids:
        if not is_text(finding_id) or finding_id not in findings:
            return False
        surface_ids = findings[finding_id].get("surface_ids")
        if not isinstance(surface_ids, list) or check.get("surface_id") not in surface_ids:
            return False
    return True


def coverage_closed(surfaces, checks, required_topics, mode, evidence, baseline, findings):
    if not surfaces or not checks or not required_topics:
        return False
    checked_surfaces = {check.get("surface_id") for check in checks.values() if is_text(check.get("surface_id"))}
    if not set(surfaces).issubset(checked_surfaces):
        return False
    checked_topics = {check.get("topic") for check in checks.values() if is_text(check.get("topic"))}
    if mode == "full" and (set(required_topics) != TOPICS or checked_topics != TOPICS):
        return False
    if mode == "quick" and not set(required_topics).issubset(checked_topics):
        return False
    terminal = ("passed", "failed", "not_applicable")
    for check in checks.values():
        surface_id = check.get("surface_id")
        topic = check.get("topic")
        kind = check.get("kind")
        if not is_text(surface_id) or surface_id not in surfaces:
            return False
        if not is_text(topic) or topic not in TOPICS:
            return False
        if not is_text(kind) or kind not in CHECK_KINDS:
            return False
        status = check.get("status")
        if status not in terminal:
            return False
        if status in ("passed", "failed") and (
            not check.get("evidence_ids") or not has_evidence_kind(check, evidence, EVIDENCE_FOR_CHECK[kind])
        ):
            return False
        if status == "failed" and not has_valid_failed_finding(check, findings):
            return False
        if status == "not_applicable" and not has_valid_exemption(check, evidence, baseline):
            return False
    return True


def release_requirements_met(project, surfaces, release, checks, findings, evidence, coverage_complete):
    if not coverage_complete:
        return False
    if any(is_placeholder(project.get(field)) for field in ("name", "baseline", "scope")):
        return False
    if any(is_placeholder(surface.get(field)) for surface in surfaces.values() for field in ("name", "location")):
        return False
    if not any(check.get("status") == "passed" for check in checks.values()):
        return False
    if any(check.get("status") not in ("passed", "not_applicable") for check in checks.values()):
        return False
    for check in checks.values():
        if check.get("status") != "passed":
            continue
        evidence_ids = check.get("evidence_ids", [])
        if not isinstance(evidence_ids, list):
            return False
        for item_id in evidence_ids:
            if not is_text(item_id) or item_id not in evidence or evidence[item_id].get("baseline_ref") != project.get("baseline"):
                return False
    for surface_id in surfaces:
        behavior_checks = [check for check in checks.values() if check.get("surface_id") == surface_id and check.get("kind") == "behavior"]
        has_behavior = any(check.get("status") == "passed" and has_evidence_kind(check, evidence, {"test", "runtime"}) for check in behavior_checks)
        has_exemption = any(check.get("status") == "not_applicable" and has_valid_exemption(check, evidence, project.get("baseline")) for check in behavior_checks)
        if not has_behavior and not has_exemption:
            return False
    unresolved = ("open", "in_progress", "fixed_unverified")
    if any(finding.get("severity") in ("P0", "P1") and finding.get("status") in unresolved for finding in findings.values()):
        return False
    decision = release.get("decision")
    if not has_authorized_decision(decision):
        return False
    release_ids = release.get("evidence_ids", [])
    if not isinstance(release_ids, list):
        return False
    passed_evidence = {
        item_id
        for check in checks.values()
        if check.get("status") == "passed" and isinstance(check.get("evidence_ids"), list)
        for item_id in check["evidence_ids"]
        if is_text(item_id)
    }
    return any(
        is_text(item_id)
        and item_id in passed_evidence
        and item_id in evidence
        and is_text(evidence[item_id].get("kind"))
        and evidence[item_id]["kind"] in {"test", "runtime"}
        and evidence[item_id].get("baseline_ref") == project.get("baseline")
        for item_id in release_ids
    )


def build_result(data, errors, warnings, coverage_complete, release_ready):
    def count(field):
        value = data.get(field, []) if isinstance(data, dict) else []
        return len(value) if isinstance(value, list) else 0

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "surfaces": count("surfaces"),
            "checks": count("checks"),
            "findings": count("findings"),
            "review_coverage_complete": coverage_complete,
            "release_ready": release_ready,
        },
    }


def validate_review(data):
    errors = []
    warnings = []
    require_fields(data, ["schema_version", "project", "surfaces", "evidence", "checks", "findings", "release"], "$", errors)
    if errors:
        return build_result(data, errors, warnings, False, False)
    if data.get("schema_version") != "1.3":
        errors.append("$.schema_version: expected 1.3")
        return build_result(data, errors, warnings, False, False)
    mode, required_topics = validate_project(data["project"], errors)
    if mode is None:
        return build_result(data, errors, warnings, False, False)
    surfaces = validate_surfaces(data["surfaces"], errors)
    evidence = validate_evidence(data["evidence"], errors)
    checks = validate_checks(data["checks"], surfaces, evidence, data["project"].get("baseline"), errors)
    findings = validate_findings(data["findings"], surfaces, evidence, checks, data["project"].get("baseline"), errors)
    for path, check in checks.items():
        validate_references(check.get("finding_ids"), findings, f"$.checks[{path}].finding_ids", errors)

    coverage_complete = not errors and coverage_closed(surfaces, checks, required_topics, mode, evidence, data["project"].get("baseline"), findings)

    release = data["release"]
    if not require_fields(release, ["status", "reason", "evidence_ids"], "$.release", errors):
        return build_result(data, errors, warnings, False, False)
    status = release.get("status")
    if status not in ("ready", "not_ready", "not_assessed"):
        errors.append("$.release.status: invalid")
    if not is_text(release.get("reason")):
        errors.append("$.release.reason: expected non-empty string")
    validate_references(release.get("evidence_ids"), evidence, "$.release.evidence_ids", errors)
    if status == "ready":
        decision = release.get("decision")
        if not has_authorized_decision(decision):
            errors.append("$.release.decision: authority and reason required for ready")
    project = data["project"]
    requirements_met = release_requirements_met(project, surfaces, release, checks, findings, evidence, coverage_complete)
    release_ready = status == "ready" and requirements_met and not errors
    if status == "ready" and not requirements_met:
        errors.append("$.release.status: ready does not satisfy the release requirements")
    if not surfaces:
        warnings.append("No surfaces registered. This is a template or an incomplete review.")
    return build_result(data, errors, warnings, coverage_complete, release_ready)


def main():
    parser = argparse.ArgumentParser(description="Validate a lean backend review ledger")
    parser.add_argument("review", type=Path)
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    try:
        if args.review.stat().st_size > MAX_REVIEW_BYTES:
            raise ValueError(f"review file too large (limit {MAX_REVIEW_BYTES} bytes)")
        data = json.loads(args.review.read_text(encoding="utf-8"))
        result = validate_review(data)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result = {"ok": False, "errors": [f"input: {exc}"], "warnings": [], "summary": {}}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        return 1
    if args.require_ready and not result["summary"]["release_ready"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
