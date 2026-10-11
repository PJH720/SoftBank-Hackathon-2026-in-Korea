"""Durable deployment checkpoints, without environment values or raw CLI output.

An interrupted external command is never replayed automatically: its side effect may
have happened already. Recovery restores confirmed outcomes and makes uncertainty
visible so an owner can inspect/redeploy instead of losing the deployment ID.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.storage import write_json


def save(directory: Path, job_id: str, snapshot: dict) -> None:
    write_json(directory / f"{job_id}.json", snapshot)


def load(directory: Path) -> list[tuple[str, dict]]:
    records = []
    for path in directory.glob("*.json"):
        # A bad checkpoint must not silently disappear from the UI after restart.
        # Propagate corruption and fail startup rather than claiming there were no jobs.
        records.append((path.stem, json.loads(path.read_text())))
    return sorted(records, key=lambda row: row[1]["created_at"])


def forget(directory: Path, job_id: str) -> None:
    (directory / f"{job_id}.json").unlink(missing_ok=True)


def recover(snapshot: dict) -> dict:
    """Use a deployer's atomic final result if the process died before checkpointing it."""
    if snapshot["completed"]:
        return snapshot
    snapshot = {**snapshot, "targets": dict(snapshot["targets"]), "live": dict(snapshot["live"])}
    for target, directory in snapshot.get("target_dirs", {}).items():
        if target in snapshot["live"] or snapshot["targets"].get(target, {}).get("success") is False:
            continue
        try:
            outcome = json.loads((Path(directory) / ".deployer" / "last_result.json").read_text())
        except (OSError, ValueError):
            continue
        # A failed attempt might have been followed by another healer attempt. Only a
        # verified successful result is conclusive; a failure is retained as interrupted.
        if outcome.get("ok") and outcome.get("target") == target:
            snapshot["targets"][target] = {"success": True, "url": outcome.get("url")}
            snapshot["live"][target] = outcome.get("handles", {})
    uncertain = not snapshot["targets"] or any(
        value.get("success") is None for value in snapshot["targets"].values()
    )
    message = (
        "Backend restarted during deployment. Unconfirmed targets were not replayed; "
        "inspect their resources before redeploying."
    )
    for target, result in snapshot["targets"].items():
        if result.get("success") is None:
            snapshot["targets"][target] = {
                "success": False,
                "url": None,
                "detail": {"message": message, "interrupted": True},
            }
    successes = [r.get("success") is True for r in snapshot["targets"].values()]
    # Pending migration cleanup is not silently repeated either.
    interrupted = uncertain or bool(snapshot.get("retire"))
    snapshot.update(
        completed=True,
        state=(
            "completed"
            if successes and all(successes) and not interrupted
            else "partial_failure"
            if any(successes)
            else "failed"
        ),
        error=message if interrupted else snapshot.get("error"),
        interrupted=interrupted,
    )
    return snapshot
