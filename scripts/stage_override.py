#!/usr/bin/env python3
"""Explicit, confirmed manual acceptance of existing Studio stage outputs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import stage_completion as completion  # noqa: E402
from scripts import stage_invalidation as invalidation  # noqa: E402
from studio import fingerprints  # noqa: E402

WARNING = (
    "This override marks existing stage outputs as valid without rerunning stage-specific success checks, "
    "historical provenance checks, or human review. Changed inputs, instructions, or code may make those "
    "outputs incorrect. Required files and inventory counts are checked, not their content or semantic "
    "coverage. Active DONE markers will be archived and dependent completion may be invalidated. "
    "No outputs will be regenerated or moved to STALE/."
)
BYPASSED = ["stage-specific-success", "historical-provenance", "human-review"]


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _history(project, definition):
    directory = project / completion.relative_path(definition["folder"])
    marker = directory / "DONE"
    invalidation._safe(marker, project)
    if marker.exists() and not marker.is_file():
        raise ValueError(f"Invalid completion marker: {marker}")
    if not marker.is_file():
        marker = completion.latest_archived_done(directory)
    if marker is None:
        return None
    invalidation._safe(marker, project)
    previous = json.loads(marker.read_text(encoding="utf-8-sig"))
    if (not isinstance(previous, dict) or previous.get("step") not in (None, definition["step"])
            or not any(_recognizable_output(entry, definition) for entry in _recorded_outputs(previous))):
        raise ValueError(f"Prior DONE lacks recognizable completion output evidence: {marker}")
    reason = None
    event = marker.parent / "EVENT.json"
    if marker.parent.parent == directory / "STALE":
        invalidation._safe(event, project)
        if event.is_file():
            metadata = invalidation._json(event)
            reason = metadata.get("reason") if isinstance(metadata.get("reason"), str) else None
    return {"scope": "project", "path": marker.relative_to(project).as_posix(),
            "sha256": fingerprints.raw_sha256(marker), "invalidation_reason": reason}


def _recorded_outputs(manifest):
    from scripts.validation import recorded_fingerprints
    return recorded_fingerprints({"outputs": manifest.get("outputs")})


def _recognizable_output(entry, definition):
    """Recognize old completion evidence, without asserting its old content is current."""
    try:
        if entry.get("scope") != "project":
            return False
        path = completion.relative_path(entry["path"])
        rules = [*definition["outputs"], *(rule for rule in definition["inputs"] if rule.get("role") == "project")]
        if not any(path.parent.as_posix() == rule["folder"]
                   and re.fullmatch(rule["filename_regex"], path.name, re.I) for rule in rules):
            return False
        method = entry.get("method", "sha256" if "sha256" in entry else "legacy-media-stat")
        if method == "sha256":
            return isinstance(entry.get("sha256"), str) and bool(re.fullmatch(r"[0-9a-fA-F]{64}", entry["sha256"]))
        if method not in ("media-stat", "legacy-media-stat") or path.suffix.lower() not in fingerprints.MEDIA_SUFFIXES:
            return False
        size = entry.get("size_bytes", entry.get("bytes"))
        stamp = entry.get("mtime_s") if method == "media-stat" else entry.get("mtime_ns")
        return type(size) is int and size >= 0 and type(stamp) is int
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def _impacts(project, stages, selected):
    """Preview all possible metadata mutations, including unknown downstream steps."""
    result = {}
    for step in selected:
        targets = invalidation._targets(project, stages, step, False)
        own = project / completion.relative_path(stages[step]["folder"])
        if own.is_dir():
            targets[step] = own
        for identifier, directory in targets.items():
            invalidation._safe(directory, project)
            lock = directory / ".DONE.lock"
            invalidation._safe(lock, project)
            if lock.exists():
                raise ValueError(f"Completion operation is active or interrupted: {lock}")
            invalidation._plan(directory, identifier)
            log = directory / "LOG.md"
            if log.is_file() and log.stat().st_nlink > 1:
                raise ValueError(f"Refusing hardlinked override/invalidation log: {log}")
            marker = directory / "DONE"
            pointer = directory / "STALE/PENDING.json"
            result[directory.relative_to(project).as_posix()] = {
                "step": identifier,
                "active_done_sha256": fingerprints.raw_sha256(marker) if marker.is_file() else None,
                "pending_sha256": fingerprints.raw_sha256(pointer) if pointer.is_file() else None,
                "outputs_moved": False,
            }
    return dict(sorted(result.items()))


def preview(project, workflow=ROOT, *, stage, reason):
    """No writes, no stage plugins; return a confirmation-bound plan."""
    from scripts.override_snapshot import collect_snapshot
    from scripts.validation import check_marker, load_stages, order_stages
    project, workflow = Path(project).resolve(strict=True), Path(workflow).resolve(strict=True)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("An explicit override reason is required")
    stages = load_stages(workflow)
    if stage != "all" and not re.fullmatch(r"[1-9]\d*\.[1-9]\d*", stage):
        raise ValueError("Use a stage.substage ID or all")
    if stage != "all" and stage not in stages:
        raise ValueError(f"Unknown stage: {stage}")
    histories, blocked, skipped = {}, {}, {}
    candidates = order_stages(stages) if stage == "all" else [stage]
    for step in candidates:
        try:
            history = _history(project, stages[step])
            if history is None:
                if stage == "all":
                    skipped[step] = "No prior completion; never-completed stages are not overridden"
                else:
                    blocked[step] = ["No active or archived DONE proving prior completion"]
                continue
            histories[step] = history
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
            histories[step] = None
            blocked[step] = [str(error)]
    selected = [step for step in order_stages(stages) if step in histories or step in blocked]
    snapshots = {}
    for step in selected:
        try:
            snapshots[step] = collect_snapshot(project, workflow, step)
            invalidation.read_pending(project / stages[step]["folder"], step)
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
            blocked.setdefault(step, []).append(str(error))
    needed = set()
    for step in selected:
        needed.update(order_stages(stages, step))
    outcomes, prerequisites = {}, {}
    for step in order_stages(stages):
        if step not in needed:
            continue
        if step in selected:
            status, explanation = (("blocked", "; ".join(blocked[step])) if step in blocked
                                   else ("current", "Will be manually accepted after confirmation"))
        else:
            status, explanation = check_marker(project, workflow, stages[step])
            marker = project / stages[step]["folder"] / "DONE"
            invalidation._safe(marker, project)
            prerequisites[step] = {"status": status, "reason": explanation,
                                   "done_sha256": fingerprints.raw_sha256(marker) if marker.is_file() else None}
        blockers = [item["step"] for item in stages[step]["prerequisites"]
                    if outcomes[item["step"]] != "current"]
        if blockers:
            status = "blocked"
            if step in selected:
                blocked.setdefault(step, []).append("Prerequisites not current or eligible: " + ", ".join(blockers))
        outcomes[step] = status
    impacts = {}
    try:
        impacts = _impacts(project, stages, selected)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
        blocked.setdefault("transition", []).append(str(error))
    if not selected:
        blocked["scope"] = ["No previously completed stages are eligible"]
    plan = {"schema": "studio.override.plan", "schema_version": 1, "project_root": str(project),
            "requested": stage, "reason": reason.strip(), "warning": WARNING, "stages": selected,
            "skipped": skipped, "blocked": blocked, "can_apply": bool(selected) and not blocked,
            "histories": histories, "snapshots": snapshots, "prerequisites": prerequisites, "impacts": impacts}
    plan["plan_id"] = _digest(plan)
    return plan


def validate_override(project, workflow, definition, manifest):
    """Honor only a well-formed manual attestation whose complete snapshot is current."""
    from scripts.override_snapshot import validate_snapshot
    from scripts.validation import check_common
    try:
        if (manifest.get("schema") != "studio.done" or type(manifest.get("schema_version")) is not int
                or manifest.get("schema_version") != 1
                or manifest.get("step") != definition["step"] or manifest.get("completion_mode") != "manual-override"):
            raise ValueError("Unsupported manual override completion record")
        assertion = manifest["override"]
        if (assertion.get("schema") != "studio.manual-override" or type(assertion.get("schema_version")) is not int
                or assertion.get("schema_version") != 1
                or assertion.get("confirmed") is not True or assertion.get("warning") != WARNING
                or assertion.get("bypassed_checks") != BYPASSED
                or not isinstance(assertion.get("reason"), str) or not assertion["reason"].strip()
                or not isinstance(assertion.get("actor"), str) or not assertion["actor"].strip()
                or not re.fullmatch(r"[0-9a-f]{64}", assertion.get("plan_id", ""))):
            raise ValueError("Incomplete manual override attestation")
        applied = datetime.fromisoformat(assertion["applied_at"])
        if applied.tzinfo is None:
            raise ValueError("Override timestamp needs a timezone")
        prior = assertion["prior_done"]
        path = completion.relative_path(prior["path"])
        folder = completion.relative_path(definition["folder"])
        if (prior.get("scope") != "project" or not re.fullmatch(r"[0-9a-fA-F]{64}", prior["sha256"])
                or path.name != "DONE" or not path.is_relative_to(folder)
                or not (path.parent == folder or (path.parent.name.isdecimal()
                        and path.parent.parent == folder / "STALE"))):
            raise ValueError("Invalid prior completion reference")
        project, workflow = Path(project).resolve(strict=True), Path(workflow).resolve(strict=True)
        invalidation._safe(project / folder / "DONE", project)
        status, reason = check_common(project, workflow, definition, manifest)
        if status != "current":
            return status, reason
        status, reason = validate_snapshot(project, workflow, definition, manifest)
        if status != "current":
            return status, reason
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError) as error:
        return "unverifiable", f"Cannot validate manual override: {error}"
    return "current", "Manual acceptance; frozen inventories and fingerprints are current (stage checks waived)"


class OverrideApplyError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__(report["error"])


def preview_report(plan):
    """Avoid duplicating entire ancestor snapshots in a human confirmation preview."""
    accepted = {"project": set(), "workflow": set()}
    counts = {}
    for step, snapshot in plan["snapshots"].items():
        counts[step] = {section: len(snapshot[section]) for section in ("inputs", "outputs", "dependencies")}
        counts[step]["output_files"] = [entry["path"] for entry in snapshot["outputs"]]
        for section in ("inputs", "outputs", "dependencies"):
            for entry in snapshot[section]:
                accepted[entry["scope"]].add(entry["path"])
    return {"warning": plan["warning"], "project_root": plan["project_root"],
            "requested": plan["requested"], "reason": plan["reason"], "plan_id": plan["plan_id"],
            "can_apply": plan["can_apply"], "stages": plan["stages"], "skipped": plan["skipped"],
            "blocked": plan["blocked"], "impacts": plan["impacts"], "inventories": counts,
            "accepted_files": {scope: sorted(paths) for scope, paths in accepted.items()}}


def apply(project, workflow=ROOT, *, stage, reason, confirm, actor):
    """Recheck confirmation before writes; report any durable partial completion."""
    from scripts.validation import load_stages
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("An explicit confirmation actor is required")
    plan = preview(project, workflow, stage=stage, reason=reason)
    if not plan["can_apply"]:
        raise ValueError("Override preflight is blocked: " + json.dumps(plan["blocked"], sort_keys=True))
    if not isinstance(confirm, str) or confirm != plan["plan_id"]:
        raise ValueError("Confirmation does not match current project evidence; preview and confirm again")
    project, workflow = Path(project).resolve(strict=True), Path(workflow).resolve(strict=True)
    stages = load_stages(workflow)
    applied = []
    for index, step in enumerate(plan["stages"]):
        committed = False
        manifest = None
        try:
            from scripts.override_snapshot import validate_snapshot
            history = _history(project, stages[step])
            if history is None or history["sha256"] != plan["histories"][step]["sha256"]:
                raise ValueError("Prior completion changed after confirmation")
            snapshot = plan["snapshots"][step]
            status, explanation = validate_snapshot(project, workflow, stages[step], snapshot)
            if status != "current":
                raise ValueError(explanation)
            for folder, impact in _impacts(project, stages, [step]).items():
                accepted = plan["impacts"].get(folder)
                if accepted is None or (impact["active_done_sha256"] is not None
                                        and impact["active_done_sha256"] != accepted["active_done_sha256"]):
                    raise ValueError(f"Dependent completion changed after confirmation: {folder}")
            timestamp = datetime.now(timezone.utc).isoformat()
            manifest = {"schema": "studio.done", "schema_version": 1, "step": step,
                        "completed_at": timestamp, "completion_mode": "manual-override", **snapshot,
                        "override": {"schema": "studio.manual-override", "schema_version": 1,
                                     "confirmed": True, "warning": WARNING, "bypassed_checks": BYPASSED,
                                     "reason": plan["reason"], "actor": actor.strip(), "applied_at": timestamp,
                                     "plan_id": confirm, "prior_done": history}}
            completion._record_manual_override(project, workflow, step, manifest)
            committed = True
            applied.append(step)
            invalidation._default_log(project / stages[step]["folder"], "Manual override accepted", [
                WARNING, f"Actor: {actor.strip()}; reason: {plan['reason']}",
                f"Confirmed scope: {stage}; plan: {confirm}; prior completion: {history['path']}",
                f"Previous invalidation reason: {history.get('invalidation_reason')}",
                "Bypassed checks: " + ", ".join(BYPASSED),
                "Accepted fingerprints: " + json.dumps({key: manifest[key] for key in
                                                         ("inputs", "outputs", "dependencies")}, sort_keys=True),
            ])
        except Exception as error:
            if not committed and (getattr(error, "completion_published", False) or _published(project, stages[step], manifest)):
                committed = True
                applied.append(step)
            raise OverrideApplyError({"status": "partial" if applied else "failed", "applied": applied,
                                      "failed_stage": step, "marker_published": committed,
                                      "remaining": plan["stages"][index + (1 if committed else 0):],
                                      "error": str(error), "requires_new_preview": True,
                                      "confirmed_impacts": plan["impacts"]}) from error
    return {"status": "overridden", "applied": applied, "skipped": plan["skipped"], "plan_id": confirm,
            "confirmed_impacts": plan["impacts"]}


def _published(project, definition, expected):
    if not isinstance(expected, dict) or expected.get("step") != definition["step"]:
        return False
    try:
        marker = project / definition["folder"] / "DONE"
        invalidation._safe(marker, project)
        actual = json.loads(marker.read_text(encoding="utf-8-sig"))
        return actual == expected
    except (OSError, UnicodeError, ValueError, TypeError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preview", "apply"])
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--stage", required=True, help="stage.substage or all")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--confirm", help="Exact plan_id from a warning preview")
    parser.add_argument("--actor", help="Label of the person explicitly confirming the warning")
    parser.add_argument("--details", action="store_true", help="Include full frozen evidence in the read-only preview")
    args = parser.parse_args()
    if args.action == "apply" and (not args.confirm or not args.actor):
        parser.error("apply requires --confirm and --actor after explicit warning confirmation")
    try:
        if args.action == "preview":
            result = preview(args.project_root, stage=args.stage, reason=args.reason)
            if not args.details:
                result = preview_report(result)
        else:
            result = apply(args.project_root, stage=args.stage, reason=args.reason,
                           confirm=args.confirm, actor=args.actor)
    except OverrideApplyError as error:
        print(json.dumps(error.report, indent=2))
        parser.exit(1)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f"Override not applied: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
