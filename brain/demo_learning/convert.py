"""Conservative demonstration conversion without coordinates or action replay."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sawon_interfaces import Observation, ShipmentRecord

from brain.cache import (
    CacheSession,
    SemanticCache,
    SemanticEntry,
    describe_target,
    matching_targets,
    record_values,
    value_matches,
)
from brain.graph.schemas import Choice, Decision
from brain.runtime.storage import atomic_write_json


class DemonstrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["0.1"] = "0.1"
    status: Literal["proposed"] = "proposed"
    recording_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation_count: int = Field(ge=0)
    entries: tuple[SemanticEntry, ...]
    rejected: dict[str, int]


def read_recording(path: Path) -> list[Observation]:
    """Read the relay's bare, newline-terminated Observation-per-line contract."""
    raw = Path(path).read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ValueError("Incomplete observation recording; original file preserved")
    result = [Observation.model_validate_json(line) for line in raw.splitlines()]
    _validate_sequence(result)
    return result


def _validate_sequence(observations: list[Observation]) -> None:
    seen = set()
    for index, observation in enumerate(observations):
        if observation.obs_id in seen:
            raise ValueError("Repeated observation identity in demonstration")
        seen.add(observation.obs_id)
        if index and observation.ts <= observations[index - 1].ts:
            raise ValueError("Demonstration observations must have increasing timestamps")


def _convert(
    observations: list[Observation], record: ShipmentRecord, cache: SemanticCache
) -> tuple[DemonstrationResult, CacheSession]:
    _validate_sequence(observations)
    session = cache.start_run()
    rejected: Counter[str] = Counter()
    fields = tuple(record_values(record))
    # A third observation must corroborate the changed value. A single transient
    # value or intermediate keystroke cannot seed a durable input recipe.
    for index in range(max(0, len(observations) - 2)):
        before, after, stable = observations[index : index + 3]
        for target in before.elements:
            descriptor = describe_target(target, before)
            if descriptor is None:
                continue
            after_targets = matching_targets(descriptor, after)
            stable_targets = matching_targets(descriptor, stable)
            if len(after_targets) != 1 or len(stable_targets) != 1:
                rejected["ambiguous_or_missing_target"] += 1
                continue
            changed, corroborated = after_targets[0], stable_targets[0]
            if target.value == changed.value:
                continue
            if not target.focused and not changed.focused:
                rejected["no_focus_evidence"] += 1
                continue
            if after.active_window != stable.active_window:
                rejected["window_changed_before_corroboration"] += 1
                continue
            sources = [field for field in fields if value_matches(field, changed.value, record)]
            if len(sources) != 1:
                rejected["ambiguous_or_unmatched_source"] += 1
                continue
            source = sources[0]
            if not value_matches(source, corroborated.value, record):
                rejected["unstable_value"] += 1
                continue
            decision = Decision(
                screen_kind="editor",
                operation="act",
                intent="fill",
                subgoal="enter",
                reason="Observed focused input changed to one record field and remained stable",
                action=Choice(type="set_text", ref=target.ref, source=source),
            )
            if not session.stage_validated(before, after, decision, record, origin="demonstration"):
                rejected["unsafe_or_insufficient_proof"] += 1
    fingerprint = hashlib.sha256(
        "\n".join(item.model_dump_json() for item in observations).encode("utf-8")
    ).hexdigest()
    result = DemonstrationResult(
        recording_sha256=fingerprint,
        observation_count=len(observations),
        entries=session.proposed_entries,
        rejected=dict(rejected),
    )
    return result, session


def convert_demonstration(
    observations: list[Observation], record: ShipmentRecord
) -> DemonstrationResult:
    # Staging performs no file I/O. The proposal-only path is never read/written.
    result, session = _convert(observations, record, SemanticCache(Path("__proposal_only__")))
    session.finish(success=False)
    return result


def install_demonstration(
    observations: list[Observation], record: ShipmentRecord, cache: SemanticCache
) -> DemonstrationResult:
    """Explicitly seed inputs after rechecking their source observations in memory.

    Installation does not load arbitrary proposal JSON and does not approve a
    shipment or D6. Future uses still require fresh lookup and runtime guards.
    """
    result, session = _convert(observations, record, cache)
    session.install_demonstration()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--install-cache", type=Path, help="Explicitly install only validated demonstration inputs"
    )
    args = parser.parse_args(argv)
    paths = [args.observations.resolve(), args.record.resolve(), args.output.resolve()]
    if args.install_cache is not None:
        paths.append(args.install_cache.resolve())
    if len(paths) != len(set(paths)):
        parser.error("Input, proposal output and cache paths must be distinct")
    observations = read_recording(args.observations)
    record = ShipmentRecord.model_validate_json(args.record.read_bytes())
    if args.install_cache is None:
        result = convert_demonstration(observations, record)
    else:
        result = install_demonstration(observations, record, SemanticCache(args.install_cache))
    atomic_write_json(args.output, result)
    print(
        json.dumps(
            {
                "status": "proposed",
                "entries": len(result.entries),
                "rejected": result.rejected,
                "installed": args.install_cache is not None,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
