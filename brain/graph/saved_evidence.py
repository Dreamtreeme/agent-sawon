"""Observed saved-record proof across a fresh query and linked detail tabs."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from brain.observe.evidence import is_record_input
from brain.visual.scene import SceneObservation

from .review import EvidenceReview, Matcher, _within, assign_result_fields, review_final
from .schemas import Decision


@dataclass
class SavedEvidence:
    """Contains observed values only; input-time facts never enter this ledger."""

    session_id: str | None = None
    origin_obs_id: str | None = None
    query_row_seen: bool = False
    query_identity: dict | None = None
    fields: dict[str, dict] = field(default_factory=dict)
    conflict: str | None = None
    detail_window: int | None = None
    detail_identity: dict | None = None
    tab_continuity: bool = False
    last_obs_id: str | None = None
    last_hash: str | None = None
    last_window: int | None = None
    last_screen: str | None = None
    seen: set[tuple] = field(default_factory=set)
    last_review: EvidenceReview | None = None

    def reset(self) -> None:
        self.__dict__.update(SavedEvidence().__dict__)

    def begin(self, origin_obs_id: str | None = None) -> None:
        self.reset()
        self.session_id = str(uuid4())
        self.origin_obs_id = origin_obs_id

    def after_action(self, decision: Decision, before: SceneObservation) -> None:
        choice = decision.action
        if choice is None or self.session_id is None:
            return
        target = next((e for e in before.elements if e.ref == choice.ref), None)
        if (
            choice.type in {"set_text", "type_text", "select_option"}
            or decision.intent in {"save", "confirm_save", "query_input", "search", "select_option"}
            or (target is not None and target.role in {"checkbox", "combobox"})
        ):
            self.reset()
            return
        # Only an observed tab in the same identified detail window carries identity.
        self.tab_continuity = bool(
            self.detail_identity
            and self.detail_window == before.active_window.handle
            and decision.intent == "select_tab"
            and target is not None
            and target.role == "tabitem"
            and choice.type == "click"
        )
        if not self.tab_continuity and choice.type not in {"wait", "focus_window"}:
            self.detail_window = None
            self.detail_identity = None
        # A result-row selection can switch records. Require a new query session.
        if target is not None and target.role in {"row", "dataitem", "listitem"}:
            from brain.observe.values import normalize

            identifier = (self.query_identity or {}).get("record_id")
            if not identifier or not any(
                _within(e, target.ref, before)
                and normalize(e.value or e.name) == normalize(identifier)
                for e in before.elements
                if e.enabled
            ):
                self.reset()

    def snapshot(self, values: dict[str, str]) -> dict:
        return {
            "session_id": self.session_id,
            "record_id": values.get("hbl_no"),
            "query_row_seen": self.query_row_seen,
            "confirmed_fields": sorted(self.fields),
            "missing_fields": sorted(set(values) - self.fields.keys()),
            "conflict": self.conflict,
            "detail_identity_observed": self.detail_identity is not None,
            "query_identity": self.query_identity,
            "detail_identity": self.detail_identity,
            "evidence": self.fields,
        }

    def _store(self, name, element, observation, values, matches: Matcher, *, explicit: bool):
        value = (
            element.value
            if element.role in {"edit", "combobox"}
            else (element.value if element.value is not None else element.name)
        )
        if not value:
            if explicit:
                self.fields.pop(name, None)
            return
        if "…" in value or "..." in value:
            return
        if not matches(name, value, values):
            if explicit:
                self.conflict = "saved_field_conflict:" + name
            return
        shot = observation.screenshot
        self.fields[name] = {
            "value": value,
            "obs_id": str(observation.obs_id),
            "capture_id": str(shot.geometry.capture_id),
            "screenshot": shot.path,
            "ref": element.ref,
            "bbox": list(element.bbox),
            "window": observation.active_window.handle,
            "record_id": values["hbl_no"],
            "session_id": self.session_id,
            "screen_kind": observation.screen_kind,
            "identity_obs_id": (self.detail_identity or self.query_identity or {}).get("obs_id"),
        }

    def observe(
        self,
        observation: SceneObservation,
        values: dict[str, str],
        matches: Matcher,
        bindings: dict[str, str],
    ) -> EvidenceReview:
        oid = str(observation.obs_id)
        if not self.session_id or oid == self.origin_obs_id:
            return EvidenceReview(
                obs_id=oid,
                status="needs_more",
                reason="fresh_query_result_required",
                missing_fields=tuple(sorted(values)),
            )
        key = (oid, tuple(sorted(bindings.items())))
        if key in self.seen and self.last_review is not None:
            return self.last_review
        self.seen.add(key)
        current_link = False
        by_ref = {e.ref: e for e in observation.elements if e.enabled}
        if observation.screen_kind == "query":
            row = review_final(observation, values, matches)
            if row.status == "blocked":
                self.conflict = row.reason
            if row.row_ref and row.matching_rows == 1:
                current_link = self.query_row_seen = True
                cells = [e for e in by_ref.values() if _within(e, row.row_ref, observation)]
                candidates = {
                    name: {e.ref for e in cells if matches(name, e.value or e.name, values)}
                    for name in values
                }
                assignment = assign_result_fields(candidates)
                for name, ref in assignment.items():
                    self._store(name, by_ref[ref], observation, values, matches, explicit=False)
                self.query_identity = self.fields.get("hbl_no")
                for name, ref in bindings.items():
                    if (
                        name in values
                        and ref in by_ref
                        and by_ref[ref].role in {"cell", "text"}
                        and _within(by_ref[ref], row.row_ref, observation)
                    ):
                        self._store(name, by_ref[ref], observation, values, matches, explicit=True)
            else:
                # A changed/empty/different result must never reuse a previous row proof.
                self.fields.clear()
                self.query_row_seen = False
                self.query_identity = None
            self.detail_window = None
            self.detail_identity = None
        elif observation.screen_kind == "editor" and self.query_row_seen:
            inputs = {r: e for r, e in by_ref.items() if is_record_input(e, observation)}
            identity = inputs.get(bindings.get("hbl_no"))
            if identity is not None:
                if matches("hbl_no", identity.value, values):
                    self.detail_window = observation.active_window.handle
                    self._store("hbl_no", identity, observation, values, matches, explicit=True)
                    self.detail_identity = self.fields.get("hbl_no")
                    current_link = True
                else:
                    self.conflict = "saved_record_identity_changed"
                    self.fields.clear()
                    self.detail_identity = None
            else:
                unchanged = (
                    self.last_hash == observation.screen_hash
                    and self.last_window == observation.active_window.handle
                )
                current_link = bool(
                    self.detail_identity
                    and self.detail_window == observation.active_window.handle
                    and self.last_screen == "editor"
                    and (self.tab_continuity or unchanged)
                )
            if current_link:
                # Every field has its own current value; one arbitrary box cannot prove many fields.
                owners: dict[str, list[str]] = {}
                for name, ref in bindings.items():
                    if name in values and ref in inputs:
                        owners.setdefault(ref, []).append(name)
                for ref, names in owners.items():
                    if len(names) > 1:
                        continue
                    self._store(names[0], inputs[ref], observation, values, matches, explicit=True)
            else:
                self.detail_window = None
                self.detail_identity = None
        else:
            self.detail_window = None
            self.detail_identity = None
        self.tab_continuity = False
        self.last_obs_id, self.last_hash = oid, observation.screen_hash
        self.last_window, self.last_screen = (
            observation.active_window.handle,
            observation.screen_kind,
        )
        missing = tuple(sorted(set(values) - self.fields.keys()))
        ready = current_link and self.query_row_seen and not missing and not self.conflict
        self.last_review = EvidenceReview(
            obs_id=oid,
            status="blocked" if self.conflict else "ready" if ready else "needs_more",
            reason=self.conflict
            or ("saved_record_evidence_complete" if ready else "saved_record_evidence_missing"),
            missing_fields=missing,
            matching_rows=int(self.query_row_seen),
        )
        return self.last_review
