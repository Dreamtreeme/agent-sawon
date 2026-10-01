"""Save errors are not persistence: repair only cited record fields, then retry."""

from uuid import uuid4

import pytest
from sawon_interfaces import ActionResult
from test_brain import FakeRelay, decision, element, run_brain, success_steps, text_choice
from test_brain import record as record


def review(result, ref, next_action, repairs=()):
    return {
        "result": result,
        "evidence_refs": [ref] if ref else [],
        "repair_fields": list(repairs),
        "next_decision": {"decision": next_action},
    }


class SaveDialogRelay(FakeRelay):
    save_attempts = 0
    dialog_kind = "rejected"

    def observe(self):
        observed = super().observe()
        if self.mode == "dialog":
            return observed.model_copy(
                update={
                    "elements": [
                        element("e0", "Window", name="Validation"),
                        element(
                            "e90",
                            "Text",
                            name="Total weight is invalid"
                            if self.dialog_kind == "rejected"
                            else "Confirm persistence of this record",
                            parent="e0",
                        ),
                        element("e91", "Button", name="Continue", parent="e0"),
                    ]
                }
            )
        return observed

    def act(self, action):
        a = action.root
        if a.type == "click" and a.target.ref == "e7":
            self.save_attempts += 1
            if self.save_attempts == 1:
                self.actions.append(action)
                self.mode = "dialog"
                return ActionResult(
                    action_id=a.action_id, ok=True, post_obs_id=uuid4(), duration_ms=0
                )
        if a.type == "click" and a.target.ref == "e91":
            self.mode = "editor" if self.dialog_kind == "rejected" else "query"
            if self.dialog_kind != "rejected":
                self.persist_inputs()
        return super().act(action)


def test_save_rejection_returns_to_entry_repairs_one_field_and_retries(tmp_path, record):
    relay = SaveDialogRelay(record)
    steps = success_steps(record)
    save = next(i for i, d in enumerate(steps) if d["intent"] == "save")
    replies = (
        steps[: save + 1]
        + [
            review(
                "rejected",
                "e90",
                decision(kind="dialog", action={"type": "click", "ref": "e91"}),
                ["gross_weight_kg"],
            ),
            decision(
                kind="editor",
                intent="fill",
                action=text_choice("gross_weight_kg", relay.field_refs["gross_weight_kg"]),
            ),
            steps[save],
        ]
        + steps[save + 1 :]
    )
    result, brain, relay, _ = run_brain(tmp_path, record, replies, relay=relay)
    assert result.status == "success", result
    assert relay.save_attempts == 2 and relay.saved
    events = brain.log.read_all()
    reviews = [
        e.payload["result"] for e in events if e.payload.get("check") == "save_result_review"
    ]
    assert reviews == ["rejected", "ready_for_query"]
    assert any(e.payload.get("reason") == "save_rejected" for e in events)


def test_save_confirmation_remains_pending_until_its_result_is_reviewed(tmp_path, record):
    relay = SaveDialogRelay(record)
    relay.dialog_kind = "confirmation"
    steps = success_steps(record)
    save = next(i for i, d in enumerate(steps) if d["intent"] == "save")
    replies = (
        steps[: save + 1]
        + [
            review(
                "confirmation_required",
                "e90",
                decision(
                    kind="dialog", intent="confirm_save", action={"type": "click", "ref": "e91"}
                ),
            )
        ]
        + steps[save + 1 :]
    )
    result, brain, _, _ = run_brain(tmp_path, record, replies, relay=relay)
    assert result.status == "success", result
    reviews = [
        e.payload["result"]
        for e in brain.log.read_all()
        if e.payload.get("check") == "save_result_review"
    ]
    assert reviews == ["confirmation_required", "ready_for_query"]


@pytest.mark.parametrize(
    "outcome,ref,reason",
    [
        ("rejected", "e90", "save_rejected_without_record_repair"),
        ("unknown", None, "save_result_unknown"),
        ("ready_for_query", "e999", "save_result_evidence_not_current"),
    ],
)
def test_unrepairable_unknown_or_stale_save_evidence_never_runs_final_query(
    tmp_path, record, outcome, ref, reason
):
    relay = SaveDialogRelay(record)
    steps = success_steps(record)
    save = next(i for i, d in enumerate(steps) if d["intent"] == "save")
    replies = steps[: save + 1] + [
        review(outcome, ref, decision("stop", intent="stop", kind="dialog"))
    ]
    result, brain, relay, _ = run_brain(tmp_path, record, replies, relay=relay)
    assert result.reason == reason
    assert not relay.saved and relay.save_attempts == 1
    assert brain.phase != "final_query"
    assert relay.actions[-1].root.target.ref == "e7"
