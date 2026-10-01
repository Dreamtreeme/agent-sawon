"""Versioned, atomic semantic input cache with success-gated promotion.

Only record-backed text inputs are reusable. Navigation, authentication, search,
save, approval and completion are deliberately never cached. A hit is a proposal
bound to the supplied observation, not permission to execute it.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

import portalocker
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator
from sawon_interfaces import Observation, ShipmentRecord, UIElement

from brain.graph.schemas import Choice, Decision, FieldName
from brain.observe.evidence import is_record_input
from brain.observe.summary import ancestors
from brain.observe.values import field_matches, values_for
from brain.runtime.storage import atomic_write_json

AUTH_TERMS = (
    "password",
    "passwd",
    "pwd",
    "username",
    "login",
    "credential",
    "비밀번호",
    "암호",
    "로그인",
    "계정",
)


class CacheIntegrityError(ValueError):
    """A corrupt or unsupported cache is preserved, never silently repaired."""


class CacheModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def record_values(record: ShipmentRecord) -> dict[FieldName, str]:
    return cast(dict[FieldName, str], values_for(record))


def value_matches(field: FieldName, actual: str | None, record: ShipmentRecord) -> bool:
    return field_matches(field, actual, values_for(record))


class SemanticContext(CacheModel):
    role: str = Field(min_length=1, max_length=100)
    label: str = Field(max_length=300)


class TargetDescriptor(CacheModel):
    role: Literal["edit", "combobox"]
    label_semantics: str = Field(min_length=1, max_length=300)
    context: tuple[SemanticContext, ...] = Field(default=(), max_length=16)


class SemanticEntry(CacheModel):
    schema_version: Literal["0.1"] = "0.1"
    task_type: Literal["shipment_entry"] = "shipment_entry"
    subgoal: Literal["enter"] = "enter"
    field: FieldName
    target_descriptor: TargetDescriptor
    action_template: Literal["set_text", "type_text"] = "set_text"
    success_count: int = Field(default=1, ge=1)
    last_verified: AwareDatetime
    failure_count: int = Field(default=0, ge=0)
    invalidated: bool = False
    origin: Literal["successful_run", "demonstration"] = "successful_run"
    evidence_source: Literal["uia", "ocr_visual"] = "uia"

    @property
    def entry_id(self) -> str:
        semantic = self.model_dump(
            mode="json",
            include={"task_type", "subgoal", "field", "target_descriptor", "action_template"},
        )
        if self.evidence_source != "uia":
            semantic["evidence_source"] = self.evidence_source
        return hashlib.sha256(
            json.dumps(semantic, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()


class CacheDocument(CacheModel):
    schema_version: Literal["0.1"] = "0.1"
    entries: tuple[SemanticEntry, ...] = ()

    @model_validator(mode="after")
    def unique_entries(self) -> CacheDocument:
        identifiers = [entry.entry_id for entry in self.entries]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Duplicate semantic cache entry")
        return self


def _safe_label(value: str) -> bool:
    folded = normalized(value)
    return "[redacted]" not in folded and not any(term in folded for term in AUTH_TERMS)


def describe_target(element: UIElement, observation: Observation) -> TargetDescriptor | None:
    """Describe only current public UI semantics, never geometry or stable IDs."""
    if observation.blocked_reason or observation.active_window is None:
        return None
    if (
        element not in observation.elements
        or not element.enabled
        or not is_record_input(element, observation)
    ):
        return None
    role = element.role.casefold()
    label = normalized(element.label_hint or element.name)
    parents = ancestors(element, observation.elements)
    if role not in {"edit", "combobox"} or not label or len(parents) > 16:
        return None
    raw_labels = [element.name, element.label_hint or ""]
    raw_labels += [part.name + " " + (part.label_hint or "") for part in parents]
    if not all(_safe_label(value) for value in raw_labels):
        return None
    # Container names are observed semantics. Learning below rejects any label
    # containing a record value, including a dynamic document/window title.
    context = tuple(
        SemanticContext(
            role=normalized(parent.role),
            label=normalized(parent.label_hint or parent.name),
        )
        for parent in parents
    )
    try:
        return TargetDescriptor(role=role, label_semantics=label, context=context)
    except ValidationError:
        return None


def matching_targets(descriptor: TargetDescriptor, observation: Observation) -> list[UIElement]:
    return [
        item for item in observation.elements if describe_target(item, observation) == descriptor
    ]


def label_anchors(
    entries: Iterable[SemanticEntry], observation: Observation, minimum: int
) -> list[tuple[SemanticEntry, UIElement]]:
    """Unique role/label matches sharing the outer semantic scope, across all fields."""
    current = [(item, describe_target(item, observation)) for item in observation.elements]
    pairs: list[tuple[SemanticEntry, UIElement]] = []
    for entry in entries:
        saved = entry.target_descriptor
        if entry.invalidated or not saved.context or not saved.context[0].label:
            continue
        targets = [
            item
            for item, desc in current
            if desc is not None
            and desc.context
            and desc.context[0] == saved.context[0]
            and (desc.role, desc.label_semantics) == (saved.role, saved.label_semantics)
        ]
        if len(targets) == 1:
            pairs.append((entry, targets[0]))
    # Inspect every learned source, including already verified fields. Filtering
    # remaining fields first could conceal a conflicting ownership claim.
    unambiguous = [
        (entry, target)
        for entry, target in pairs
        if len({other.field for other, item in pairs if item.ref == target.ref}) == 1
        and len({item.ref for other, item in pairs if other.field == entry.field}) == 1
    ]
    return unambiguous if len({entry.field for entry, _ in unambiguous}) >= minimum else []


def _contains_record_values(descriptor: TargetDescriptor, record: ShipmentRecord) -> bool:
    text = " ".join([descriptor.label_semantics, *(part.label for part in descriptor.context)])
    # Deliberately conservative even for short values: a miss is preferable to
    # persisting business data disguised as a UI label.
    return any(
        normalized(value) in text for value in record_values(record).values() if value.strip()
    )


@dataclass(frozen=True)
class CacheMatch:
    entry_id: str
    obs_id: UUID
    decision: Decision
    match_mode: Literal["exact", "label_anchor", "model_rebind"] = "exact"


class SemanticCache:
    def __init__(
        self,
        path: Path,
        *,
        enabled: bool = True,
        max_retries: int = 1,
        lock_timeout: float = 10,
        matching_mode: Literal["exact", "rebind"] = "exact",
        min_anchor_fields: int = 2,
    ):
        if max_retries < 0:
            raise ValueError("max_retries must be nonnegative")
        self.path = Path(path)
        self.enabled = enabled
        self.max_retries = max_retries
        self.lock_timeout = lock_timeout
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        if matching_mode not in {"exact", "rebind"} or not 2 <= min_anchor_fields <= 11:
            raise ValueError("Invalid cache matching policy")
        self.matching_mode = matching_mode
        self.min_anchor_fields = min_anchor_fields

    def _read_unlocked(self) -> CacheDocument:
        if not self.path.exists():
            return CacheDocument()
        try:
            return CacheDocument.model_validate_json(self.path.read_bytes())
        except (ValidationError, UnicodeDecodeError) as exc:
            raise CacheIntegrityError(
                "Invalid semantic cache; preserved without modification"
            ) from exc

    def entries(self) -> tuple[SemanticEntry, ...]:
        if not self.enabled:
            return ()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(self.lock_path, mode="a", timeout=self.lock_timeout):
            return self._read_unlocked().entries

    def start_run(self) -> CacheSession:
        return CacheSession(self)

    def _promote(self, entries: Iterable[SemanticEntry]) -> int:
        if not self.enabled:
            return 0
        proposed = list(entries)
        if not proposed:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(self.lock_path, mode="a", timeout=self.lock_timeout):
            existing = {item.entry_id: item for item in self._read_unlocked().entries}
            for item in proposed:
                old = existing.get(item.entry_id)
                existing[item.entry_id] = item.model_copy(
                    update={
                        "success_count": 1 if old is None else old.success_count + 1,
                        "failure_count": 0,
                        "invalidated": False,
                    }
                )
            atomic_write_json(self.path, CacheDocument(entries=tuple(existing.values())))
        return len(proposed)

    def record_failure(self, entry_id: str) -> bool:
        """Persist a verified failure; return whether it is now invalidated."""
        if not self.enabled:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(self.lock_path, mode="a", timeout=self.lock_timeout):
            document = self._read_unlocked()
            changed: list[SemanticEntry] = []
            invalidated = False
            for item in document.entries:
                if item.entry_id == entry_id:
                    failures = item.failure_count + 1
                    invalidated = item.invalidated or failures > self.max_retries
                    item = item.model_copy(
                        update={"failure_count": failures, "invalidated": invalidated}
                    )
                changed.append(item)
            atomic_write_json(self.path, CacheDocument(entries=tuple(changed)))
            return invalidated


class CacheSession:
    """Uncommitted validated input proposals scoped to one complete agent run."""

    def __init__(self, cache: SemanticCache):
        self.cache = cache
        self._staged: dict[str, SemanticEntry] = {}
        self._closed = False
        self.last_miss_reason = "not_queried"
        self.rebound: dict[str, SemanticEntry] = {}
        self.rebound_modes: dict[str, str] = {}

    def lookup(
        self,
        observation: Observation,
        *,
        phase: str,
        allowed_sources: Iterable[FieldName],
        record: ShipmentRecord | None = None,
    ) -> CacheMatch | None:
        self.last_miss_reason = "no_matching_entry"
        if not self.cache.enabled or self._closed or phase != "enter":
            self.last_miss_reason = "disabled_or_ineligible"
            return None
        allowed = set(allowed_sources)
        saved_entries = tuple(
            entry
            for entry in self.cache.entries()
            if entry.evidence_source == getattr(observation, "evidence_source", "uia")
        )
        entries = (*saved_entries, *self.rebound.values())
        anchors = (
            label_anchors(saved_entries, observation, self.cache.min_anchor_fields)
            if self.cache.matching_mode == "rebind"
            else []
        )
        matches: list[tuple[SemanticEntry, UIElement, str]] = []
        for entry in entries:
            if (
                entry.invalidated
                or entry.field not in allowed
                or entry.evidence_source != getattr(observation, "evidence_source", "uia")
            ):
                continue
            targets = matching_targets(entry.target_descriptor, observation)
            mode = self.rebound_modes.get(entry.entry_id, "exact")
            if not targets and self.cache.matching_mode == "rebind" and mode != "model_rebind":
                targets = [
                    item for candidate, item in anchors if candidate.entry_id == entry.entry_id
                ]
                mode = "label_anchor"
            if len(targets) != 1:
                continue
            if mode == "label_anchor" and entry.entry_id not in self.rebound:
                local = entry.model_copy(
                    update={
                        "target_descriptor": describe_target(targets[0], observation),
                        "failure_count": 0,
                        "invalidated": False,
                    }
                )
                if any(e.entry_id == local.entry_id and e.invalidated for e in saved_entries):
                    continue
                entry = self.rebound.setdefault(local.entry_id, local)
                self.rebound_modes[entry.entry_id] = "label_anchor"
                if entry.invalidated:
                    continue
            if record is not None and value_matches(entry.field, targets[0].value, record):
                continue
            matches.append((entry, targets[0], mode))
        # A control assigned to conflicting source fields must not be guessed.
        for _, target, _ in matches:
            if len({entry.field for entry, other, _ in matches if other.ref == target.ref}) > 1:
                self.last_miss_reason = "ambiguous_source"
                return None
        for entry, _, _ in matches:
            if len({target.ref for other, target, _ in matches if other.field == entry.field}) > 1:
                self.last_miss_reason = "ambiguous_target"
                return None
        if not matches:
            return None
        entry, target, mode = sorted(matches, key=lambda pair: (pair[0].field, pair[0].entry_id))[0]
        return CacheMatch(
            entry_id=entry.entry_id,
            obs_id=observation.obs_id,
            match_mode=mode,
            decision=Decision(
                screen_kind="editor",
                operation="act",
                intent="fill",
                subgoal="enter",
                reason="Reuse a verified semantic input mapping against the current observation",
                action=Choice(type=entry.action_template, ref=target.ref, source=entry.field),
            ),
        )

    def stage_validated(
        self,
        before: Observation,
        after: Observation,
        decision: Decision,
        record: ShipmentRecord,
        *,
        origin: Literal["successful_run", "demonstration"] = "successful_run",
    ) -> bool:
        if self._closed or not self.cache.enabled:
            return False
        if getattr(before, "evidence_source", "uia") != getattr(after, "evidence_source", "uia"):
            return False
        action = decision.action
        if (
            decision.operation != "act"
            or decision.intent != "fill"
            or decision.screen_kind != "editor"
            or action is None
            or action.type not in {"set_text", "type_text"}
        ):
            return False
        values = record_values(record)
        if action.source not in values or before.obs_id == after.obs_id or before.ts >= after.ts:
            return False
        if before.active_window is None or after.active_window != before.active_window:
            return False
        target = next((item for item in before.elements if item.ref == action.ref), None)
        if target is None:
            return False
        descriptor = describe_target(target, before)
        if descriptor is None or _contains_record_values(descriptor, record):
            return False
        if len(matching_targets(descriptor, before)) != 1:
            return False
        candidates = matching_targets(descriptor, after)
        if len(candidates) != 1 or not value_matches(action.source, candidates[0].value, record):
            return False
        # A before/after pair that was already correct does not prove input.
        if value_matches(action.source, target.value, record):
            return False
        entry = SemanticEntry(
            field=action.source,
            target_descriptor=descriptor,
            action_template=action.type,
            last_verified=after.ts,
            origin=origin,
            evidence_source=getattr(after, "evidence_source", "uia"),
        )
        self._staged[entry.entry_id] = entry
        return True

    @property
    def proposed_entries(self) -> tuple[SemanticEntry, ...]:
        return tuple(self._staged.values())

    @property
    def closed(self) -> bool:
        return self._closed

    def record_failure(self, entry_id: str) -> bool:
        self._staged.pop(entry_id, None)
        if entry_id in self.rebound:
            entry = self.rebound[entry_id]
            failures = entry.failure_count + 1
            invalidated = failures > self.cache.max_retries
            self.rebound[entry_id] = entry.model_copy(
                update={"failure_count": failures, "invalidated": invalidated}
            )
            return invalidated
        return self.cache.record_failure(entry_id)

    def finish(self, *, success: bool) -> int:
        if self._closed:
            raise RuntimeError("Cache session was already closed")
        self._closed = True
        try:
            return self.cache._promote(self._staged.values()) if success else 0
        finally:
            self._staged.clear()
            self.rebound.clear()
            self.rebound_modes.clear()

    def install_demonstration(self) -> int:
        """Explicit installation of only converter-validated demonstration steps."""
        if any(item.origin != "demonstration" for item in self._staged.values()):
            raise ValueError("Only demonstration proof may seed an incomplete run")
        return self.finish(success=True)
