"""Observed saved-record proof across a fresh query and linked detail tabs."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from brain.visual.scene import SceneObservation

from .evidence_projection import EditorProjectionRequest, project_editor_evidence
from .review import EvidenceReview, Matcher, _within, assign_result_fields, review_final
from .schemas import Decision


@dataclass
class SavedEvidence:
    """Contains observed values only; input-time facts never enter this ledger."""

    identity_field: str = "hbl_no"
    session_id: str | None = None
    origin_obs_id: str | None = None
    query_row_seen: bool = False
    query_identity: dict | None = None
    submitted_query_identity: dict | None = None
    query_row_ref: str | None = None
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
        self.__dict__.update(SavedEvidence(identity_field=self.identity_field).__dict__)

    def begin(self, origin_obs_id: str | None = None) -> None:
        self.reset()
        self.session_id = str(uuid4())
        self.origin_obs_id = origin_obs_id

    def remember_submitted_query(self, observation: SceneObservation, record_id: str) -> bool:
        """Retain the exact observed filter value that produced the next result."""

        from brain.observe.values import normalize

        matches = [
            element
            for element in observation.elements
            if element.enabled
            and element.role.casefold() == "edit"
            and normalize(element.value) == normalize(record_id)
        ]
        if len(matches) != 1:
            return False
        element = matches[0]
        self.submitted_query_identity = {
            "value": element.value,
            "obs_id": str(observation.obs_id),
            "capture_id": str(observation.screenshot.geometry.capture_id),
            "screenshot": observation.screenshot.path,
            "ref": element.ref,
            "bbox": list(element.bbox),
            "window": observation.active_window.handle,
            "record_id": record_id,
            "session_id": self.session_id,
            "screen_kind": observation.screen_kind,
            "explicit": True,
            "proof_kind": "submitted_query_input",
        }
        return True

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
            exact_row_identity = bool(
                identifier
                and any(
                    _within(e, target.ref, before)
                    and normalize(e.value or e.name) == normalize(identifier)
                    for e in before.elements
                    if e.enabled
                )
            )
            selected_query_row = bool(
                self.query_row_ref == target.ref
                and self.last_obs_id == str(before.obs_id)
                and self.last_screen == "query"
                and self.query_identity
            )
            # The visual executor renews an identical capture immediately before
            # input. Element refs are observation-scoped, so the same sole row
            # receives a new ref even though its pixels and query identity did not
            # change. Preserve that proven row link across the renewal; the detail
            # screen must still expose the exact record identity before any detail
            # values can be accepted.
            if not selected_query_row and self.query_row_seen and self.query_identity:
                record_id = self.query_identity.get("record_id")
                current_rows = [
                    element
                    for element in before.elements
                    if element.enabled
                    and element.role.casefold() in {"row", "dataitem", "listitem"}
                ]
                exact_inputs = [
                    element
                    for element in before.elements
                    if element.enabled
                    and element.role.casefold() == "edit"
                    and normalize(element.value) == normalize(record_id or "")
                ]
                selected_query_row = bool(
                    self.last_hash == before.screen_hash
                    and self.last_window == before.active_window.handle
                    and self.last_screen == "query"
                    and len(current_rows) == 1
                    and current_rows[0].ref == target.ref
                    and len(exact_inputs) == 1
                )
            if not exact_row_identity and not selected_query_row:
                self.reset()

    def snapshot(self, values: dict[str, str]) -> dict:
        return {
            "session_id": self.session_id,
            "identity_field": self.identity_field,
            "record_id": values.get(self.identity_field),
            "query_row_seen": self.query_row_seen,
            "confirmed_fields": sorted(self.fields),
            "missing_fields": sorted(set(values) - self.fields.keys()),
            "conflict": self.conflict,
            "detail_identity_observed": self.detail_identity is not None,
            "query_identity": self.query_identity,
            "submitted_query_identity": self.submitted_query_identity,
            "query_row_ref": self.query_row_ref,
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
        # Query grids use both a Unicode ellipsis and two/three trailing ASCII
        # dots for clipped cells. A clipped preview is missing evidence, not a
        # contradictory saved value; the detail view must supply the full text.
        if value.rstrip().endswith(("…", "..")):
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
            "record_id": values[self.identity_field],
            "session_id": self.session_id,
            "screen_kind": observation.screen_kind,
            "identity_obs_id": (self.detail_identity or self.query_identity or {}).get("obs_id"),
        }

    def _observe_editor(
        self,
        observation: SceneObservation,
        values: dict[str, str],
        matches: Matcher,
        bindings: dict[str, str],
    ) -> bool:
        projection = project_editor_evidence(
            EditorProjectionRequest(
                observation=observation,
                values=values,
                bindings=bindings,
                identity_field=self.identity_field,
                detail_identity_present=self.detail_identity is not None,
                detail_window=self.detail_window,
                tab_continuity=self.tab_continuity,
                last_hash=self.last_hash,
                last_window=self.last_window,
                last_screen=self.last_screen,
            ),
            matches,
        )
        if projection.identity_conflict:
            self.conflict = "saved_record_identity_changed"
            self.fields.clear()
            self.detail_identity = None
        if not projection.linked:
            self.detail_window = None
            self.detail_identity = None
            return False
        if projection.identity is not None:
            self.detail_window = observation.active_window.handle
            self._store(
                self.identity_field,
                projection.identity,
                observation,
                values,
                matches,
                explicit=True,
            )
            self.detail_identity = self.fields.get(self.identity_field)
        for name, element in projection.automatic:
            self._store(name, element, observation, values, matches, explicit=False)
        for name, element in projection.explicit:
            self._store(name, element, observation, values, matches, explicit=True)
        for name in projection.ambiguous_fields:
            self.fields.pop(name, None)
        return True

    def _query_identity_input(self, by_ref, bindings, values, matches: Matcher):
        identity = by_ref.get(bindings.get(self.identity_field))
        valid = bool(
            identity is not None
            and identity.role.casefold() == "edit"
            and matches(self.identity_field, identity.value, values)
        )
        if valid:
            return identity
        exact = [
            element
            for element in by_ref.values()
            if element.role.casefold() == "edit"
            and matches(self.identity_field, element.value, values)
        ]
        return exact[0] if len(exact) == 1 else identity

    def _query_identity_anchor(
        self, identity, candidate_rows, observation, by_ref, values, matches: Matcher
    ):
        anchor = (
            identity
            if identity is not None
            and identity.role.casefold() == "edit"
            and matches(self.identity_field, identity.value, values)
            else None
        )
        if not self.query_row_seen or len(candidate_rows) != 1:
            return anchor
        current_row = candidate_rows[0]
        exact = [
            element
            for element in by_ref.values()
            if element.role.casefold() in {"edit", "text"}
            and matches(self.identity_field, element.value or element.name, values)
            and not _within(element, current_row.ref, observation)
        ]
        return exact[0] if len(exact) == 1 else anchor

    def _carried_query_row(
        self,
        row_ref,
        matching_rows,
        identity_anchor,
        candidate_rows,
        observation,
        by_ref,
        values,
        matches: Matcher,
    ):
        eligible = (
            row_ref is None
            and self.query_row_seen
            and self.query_identity
            and identity_anchor is not None
            and self.last_screen == "query"
            and self.last_window == observation.active_window.handle
            and len(candidate_rows) == 1
        )
        if not eligible:
            return row_ref, matching_rows
        current_row = candidate_rows[0]
        current_cells = [
            element
            for element in by_ref.values()
            if element.ref != current_row.ref and _within(element, current_row.ref, observation)
        ]
        prior_fields = {
            name
            for name, evidence in self.fields.items()
            if name != self.identity_field
            and evidence.get("screen_kind") == "query"
            and evidence.get("session_id") == self.session_id
        }
        carried = bool(prior_fields) and all(
            any(matches(name, item.value or item.name, values) for item in current_cells)
            for name in prior_fields
        )
        return (current_row.ref, 1) if carried else (row_ref, matching_rows)

    def _store_query_row(
        self,
        row_ref,
        identity_anchor,
        observation,
        by_ref,
        bindings,
        values,
        matches: Matcher,
    ) -> None:
        self.query_row_seen = True
        self.query_row_ref = row_ref
        cells = [element for element in by_ref.values() if _within(element, row_ref, observation)]
        candidates = {
            name: {
                element.ref
                for element in cells
                if matches(name, element.value or element.name, values)
            }
            for name in values
        }
        for name, ref in assign_result_fields(candidates).items():
            self._store(name, by_ref[ref], observation, values, matches, explicit=False)
        self.query_identity = self.fields.get(self.identity_field) or self.submitted_query_identity
        owners: dict[str, list[str]] = {}
        for name, ref in bindings.items():
            if name in values:
                owners.setdefault(ref, []).append(name)
        for ref, names in owners.items():
            if len(names) > 1 and set(names) != {"packages.count", "packages.unit"}:
                continue
            if (
                ref not in by_ref
                or by_ref[ref].role not in {"cell", "text"}
                or not _within(by_ref[ref], row_ref, observation)
            ):
                continue
            for name in names:
                if (
                    name == self.identity_field
                    and identity_anchor is not None
                    and ref != identity_anchor.ref
                ):
                    continue
                self._store(name, by_ref[ref], observation, values, matches, explicit=True)

    def _observe_query(
        self,
        observation: SceneObservation,
        values: dict[str, str],
        matches: Matcher,
        bindings: dict[str, str],
        by_ref: dict,
        selected_row_ref: str | None,
    ) -> bool:
        review = review_final(observation, values, matches, self.identity_field)
        if review.status == "blocked":
            self.conflict = review.reason
        row_ref, matching_rows = review.row_ref, review.matching_rows
        identity_input = self._query_identity_input(by_ref, bindings, values, matches)
        candidate_rows = [
            element
            for element in by_ref.values()
            if element.role.casefold() in {"row", "dataitem", "listitem"}
        ]
        identity_anchor = self._query_identity_anchor(
            identity_input, candidate_rows, observation, by_ref, values, matches
        )
        row_ref, matching_rows = self._carried_query_row(
            row_ref,
            matching_rows,
            identity_anchor,
            candidate_rows,
            observation,
            by_ref,
            values,
            matches,
        )
        selected_row_is_bound = bool(
            row_ref is None
            and selected_row_ref is not None
            and len(candidate_rows) == 1
            and candidate_rows[0].ref == selected_row_ref
            and (
                (
                    identity_input is not None
                    and identity_input.role.casefold() == "edit"
                    and matches(self.identity_field, identity_input.value, values)
                )
                or (
                    self.submitted_query_identity is not None
                    and self.submitted_query_identity.get("record_id")
                    == values[self.identity_field]
                )
            )
        )
        if selected_row_is_bound:
            row_ref, matching_rows = selected_row_ref, 1
            if identity_input is not None and identity_input.role.casefold() == "edit":
                self._store(
                    self.identity_field,
                    identity_input,
                    observation,
                    values,
                    matches,
                    explicit=True,
                )
        current_link = bool(row_ref and matching_rows == 1)
        if current_link:
            self._store_query_row(
                row_ref,
                identity_anchor,
                observation,
                by_ref,
                bindings,
                values,
                matches,
            )
        else:
            self.fields.clear()
            self.query_row_seen = False
            self.query_identity = None
            self.query_row_ref = None
        self.detail_window = None
        self.detail_identity = None
        return current_link

    def _finish_observation(
        self,
        observation: SceneObservation,
        values: dict[str, str],
        *,
        current_link: bool,
    ) -> EvidenceReview:
        oid = str(observation.obs_id)
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

    def observe(
        self,
        observation: SceneObservation,
        values: dict[str, str],
        matches: Matcher,
        bindings: dict[str, str],
        *,
        selected_row_ref: str | None = None,
    ) -> EvidenceReview:
        oid = str(observation.obs_id)
        if not self.session_id or oid == self.origin_obs_id:
            return EvidenceReview(
                obs_id=oid,
                status="needs_more",
                reason="fresh_query_result_required",
                missing_fields=tuple(sorted(values)),
            )
        key = (oid, tuple(sorted(bindings.items())), selected_row_ref)
        if key in self.seen and self.last_review is not None:
            return self.last_review
        self.seen.add(key)
        current_link = False
        by_ref = {e.ref: e for e in observation.elements if e.enabled}
        if observation.screen_kind == "query":
            current_link = self._observe_query(
                observation,
                values,
                matches,
                bindings,
                by_ref,
                selected_row_ref,
            )
        elif observation.screen_kind == "editor" and self.query_row_seen:
            current_link = self._observe_editor(observation, values, matches, bindings)
        else:
            self.detail_window = None
            self.detail_identity = None
        return self._finish_observation(observation, values, current_link=current_link)
