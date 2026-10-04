"""Application-independent replay/fake tests of the agent's evidence gates."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sawon_interfaces import ActionResult, Observation, ShipmentRecord, UIElement, WindowInfo

from brain.graph.engine import Brain, GuardStop, field_matches, values_for
from brain.graph.schemas import Decision, Plan
from brain.observe import redact, summarize
from brain.observe.rows import row_refs
from brain.relay import HttpRelay
from brain.run import main
from brain.runtime.storage import RunLog, StateStore
from brain.settings import BrainSettings
from models.base import MeasuredModelClient, ProviderReply, TokenUsage
from models.budget import CallBudget
from models.pricing import PriceRate


@pytest.fixture
def record():
    values = {
        "hbl_no": "TEST-A01",
        "mbl_no": "TEST-PARENT",
        "shipper": "SAMPLE SUPPLIER",
        "consignee": "SAMPLE CUSTOMER",
        "description": "Synthetic widgets",
        "packages": {"count": 12, "unit": "CT"},
        "gross_weight_kg": 1250.5,
        "container_no": "TEST-C01",
        "pol": "AAONE",
        "pod": "BBTWO",
    }
    return ShipmentRecord.model_validate(
        {
            "case_id": "fake-case",
            "origin": "manual",
            "fields": {name: {"value": value} for name, value in values.items()},
        }
    )


def element(ref, role, *, name="", value=None, parent=None, **kwargs):
    return UIElement(
        ref=ref,
        role=role,
        name=name,
        value=value,
        parent_ref=parent,
        bbox=(0.0, 0.0, 100.0, 25.0),
        **kwargs,
    )


class FakeRelay:
    def __init__(self, record, *, duplicate=False, corrupt=False, blocked=False):
        self.values = values_for(record)
        self.mode = "query"
        self.saved = duplicate
        self.saved_snapshot = self.values.copy() if duplicate else {}
        self.corrupt = corrupt
        self.blocked = blocked
        self.inputs = {field: "" for field in self.values}
        self.query = ""
        self.actions = []
        self.calls = 0
        self.ref_shift = 0
        self.change_refs = False
        self.field_refs = {field: f"e{index + 20}" for index, field in enumerate(self.values)}

    def health(self):
        return {"ok": True}

    def observe(self):
        self.calls += 1
        window = WindowInfo(handle=1, title="Test desktop", process="fixture.exe")
        items = [element("e0", "Window", name="Test desktop")]
        if self.mode == "query":
            items += [
                element("e1", "Edit", name="Query identifier", value=self.query, parent="e0"),
                element("e2", "Button", name="Run query", parent="e0"),
                element("e3", "Button", name="New object", parent="e0"),
                element("e4", "DataGrid", name="Result table", parent="e0"),
                element("e5", "HeaderItem", name="Identifier", parent="e4"),
            ]
            if self.saved:
                items.append(element("e6", "DataItem", name="Row 1", parent="e4"))
                items.extend(
                    element(f"e{index + 40}", "DataItem", value=value, parent="e6")
                    for index, value in enumerate(self.saved_snapshot.values())
                )
        elif self.mode == "editor":
            items.extend(
                element(
                    self.field_refs[field],
                    "Edit",
                    name=f"Input {index + 1}",
                    value=self.inputs[field],
                    parent="e0",
                )
                for index, field in enumerate(self.values)
            )
            items.append(element("e7", "Button", name="Persist object", parent="e0"))
        else:
            items += [
                element("e8", "Edit", name="", label_hint="password", value=None, parent="e0")
            ]
        if self.change_refs:
            self.ref_shift += 100
            mapping = {item.ref: f"e{int(item.ref[1:]) + self.ref_shift}" for item in items}
            items = [
                item.model_copy(
                    update={
                        "ref": mapping[item.ref],
                        "parent_ref": mapping.get(item.parent_ref),
                    }
                )
                for item in items
            ]
        return Observation(
            obs_id=uuid4(),
            ts=datetime.now(UTC),
            active_window=window,
            windows=[window],
            elements=items,
            screen_hash="0" * 40,
            blocked_reason="outside_allowlist" if self.blocked else None,
        )

    def act(self, action):
        self.actions.append(action)
        item = action.root
        if item.type in {"set_text", "type_text"}:
            ref = item.target.ref
            if self.change_refs:
                ref = f"e{int(ref[1:]) - self.ref_shift}"
            if ref == "e1":
                self.query = "corrupted" if self.corrupt else item.text
            else:
                field = next((key for key, value in self.field_refs.items() if value == ref), None)
                if field:
                    self.inputs[field] = item.text
        elif item.type == "click":
            if item.target.ref == "e3":
                self.mode = "editor"
            elif item.target.ref == "e7":
                self.persist_inputs()
                self.mode = "query"
        return ActionResult(
            action_id=item.action_id,
            ok=True,
            post_obs_id=uuid4(),
            duration_ms=0.0,
        )

    def persist_inputs(self):
        """Keep saved results separate from expected values and later editor changes."""
        self.saved_snapshot = self.inputs.copy()
        self.saved = True


def decision(operation="act", *, intent="navigate", kind="query", action=None, **kwargs):
    return {
        "screen_kind": kind,
        "operation": operation,
        "intent": intent,
        "subgoal": "Synthetic subgoal",
        "reason": "Operate using observed semantics",
        "action": action,
        **kwargs,
    }


def text_choice(field, ref):
    return {"type": "set_text", "ref": ref, "source": field}


def test_generic_task_save_enters_shared_save_review(tmp_path, record):
    relay = FakeRelay(record)
    relay.mode = "editor"
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="brain-test")
    model = ScriptedModel(log, [])
    brain = Brain(
        record=record,
        goal="Register a shipment",
        relay=relay,
        model=model,
        settings=BrainSettings(),
        case_dir=tmp_path,
        run_log=log,
    )
    brain.phase = "task"
    brain.observation = relay.observe()
    proposed = Decision.model_validate(
        decision(intent="save", kind="editor", action={"type": "click", "ref": "e7"})
    )
    assert proposed.action is not None
    wire_action = brain._wire_action(proposed.action, proposed)

    brain._accept_dispatched_action(
        wire_action,
        proposed,
        proposed.action,
        progress_scope="task:task-action:0",
        point_refined=False,
    )

    assert brain.save_issued
    assert brain.phase == "save_result"


class ScriptedModel(MeasuredModelClient):
    def __init__(self, log, decisions, *, price=Decimal("0"), unknown=False):
        super().__init__(provider="fake", model="fixture", run_log=log)
        self.decisions = iter(decisions)
        self.prompts = []
        self.price, self.unknown = price, unknown

    def _generate(self, prompt, schema, *, images=None):
        self.prompts.append(prompt)
        if self.unknown:
            raise OSError("secret provider failure")
        if schema is Plan:
            response = {"subgoals": ["Check duplication", "Enter and verify", "Save and query"]}
        else:
            response = next(self.decisions)
            if callable(response):
                response = response(prompt)
            if schema.__name__ == "SaveReviewResponse" and "result" not in response:
                # Existing success scripts explicitly move into the visible query.
                # Error/confirmation tests provide their own save-review responses.
                response = {
                    "result": "ready_for_query",
                    "evidence_refs": [(response.get("action") or {}).get("ref") or "e0"],
                    "repair_fields": [],
                    "next_decision": {"decision": response},
                }
            elif "decision" in schema.model_fields:
                response = {"decision": response}
        return ProviderReply(
            text=json.dumps(response),
            usage=TokenUsage(input_tokens=1, response_tokens=1),
        )

    def _cost(self, usage):
        return self.price


def run_brain(
    tmp_path,
    record,
    decisions,
    *,
    relay=None,
    settings=None,
    price=Decimal(0),
    unknown=False,
    password=None,
    call_budget=None,
):
    relay = relay or FakeRelay(record)
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="brain-test")
    model = ScriptedModel(log, decisions, price=price, unknown=unknown)
    brain = Brain(
        record=record,
        goal="Enter the record and save for approval",
        relay=relay,
        model=model,
        settings=settings or BrainSettings(),
        case_dir=tmp_path,
        run_log=log,
        password=password,
        call_budget=call_budget,
    )
    return brain.run(), brain, relay, model


def query_steps():
    return [
        decision(intent="query_input", action=text_choice("hbl_no", "e1")),
        decision(intent="search", action={"type": "click", "ref": "e2"}),
    ]


def success_steps(record):
    values = values_for(record)
    return [
        *query_steps(),
        decision("duplicate_clear", intent="verify", grid_ref="e4"),
        decision(action={"type": "click", "ref": "e3"}),
        *(
            decision(intent="fill", kind="editor", action=text_choice(field, f"e{index + 20}"))
            for index, field in enumerate(values)
        ),
        decision(intent="save", kind="editor", action={"type": "click", "ref": "e7"}),
        *query_steps(),
        decision(
            "complete",
            intent="verify",
            grid_ref="e4",
            row_ref="e6",
            evidence=[
                {"field": field, "ref": f"e{index + 40}"} for index, field in enumerate(values)
            ],
        ),
    ]


def test_rematches_input_when_refs_change_every_observation(tmp_path, record):
    relay = FakeRelay(record)
    relay.change_refs = True

    def choose(prompt):
        summary = json.loads(prompt.split("\nUI_TEXT=", 1)[1])
        ref = next(item["ref"] for item in summary["elements"] if item["role"] == "Edit")
        return decision(intent="query_input", action=text_choice("hbl_no", ref))

    result, brain, _, _ = run_brain(
        tmp_path, record, [choose, decision("stop", intent="stop")], relay=relay
    )
    assert result.reason == "model_requested_stop"
    assert brain.query_value_verified
    assert any(
        event.payload.get("check") == "input_value_matches" for event in brain.log.read_all()
    )


def test_blocked_foreground_stops_before_decision_or_input(tmp_path, record):
    result, _, relay, model = run_brain(tmp_path, record, [], relay=FakeRelay(record, blocked=True))
    assert result.reason == "blocked_foreground"
    assert len(model.prompts) == 1
    assert not relay.actions


def test_unknown_window_stops(tmp_path, record):
    result, _, relay, _ = run_brain(
        tmp_path, record, [decision("stop", intent="stop", kind="unknown")]
    )
    assert result.reason == "unknown_window_type"
    assert not relay.actions


def test_zero_cap_does_not_make_model_call(tmp_path, record):
    result, _, _, model = run_brain(tmp_path, record, [], settings=BrainSettings(max_cost_usd=0))
    assert result.reason == "case_cost_cap"
    assert not model.prompts


def test_call_reserve_stops_before_next_call(tmp_path, record):
    result, _, _, model = run_brain(
        tmp_path, record, [], price=Decimal("0.08"), settings=BrainSettings(max_cost_usd=0.1)
    )
    assert result.reason == "case_cost_cap"
    assert len(model.prompts) == 1


def test_unknown_failed_call_cost_forces_stop(tmp_path, record):
    result, brain, _, model = run_brain(tmp_path, record, [], unknown=True)
    assert result.reason == "unknown_model_call_cost"
    assert len(model.prompts) == 1
    assert (
        next(event for event in brain.log.read_all() if event.kind == "model_call").cost.usd is None
    )


def test_dynamic_cost_reservation_prevents_first_call(tmp_path, record):
    budget = CallBudget(PriceRate(input_usd_per_million=1000, output_usd_per_million=1000), 4096)
    result, _, _, model = run_brain(tmp_path, record, [], call_budget=budget)
    assert result.reason == "case_cost_cap"
    assert not model.prompts


def test_max_steps_stops_even_successful_waits(tmp_path, record):
    result, _, relay, model = run_brain(
        tmp_path,
        record,
        [
            decision(intent="wait", action={"type": "wait", "duration_ms": 0}),
        ],
        settings=BrainSettings(max_steps=1),
    )
    assert result.reason == "max_steps"
    assert len(model.prompts) == 2
    assert len(relay.actions) == 1


def test_password_resolved_only_at_relay_and_not_in_prompts_or_logs(tmp_path, record):
    relay = FakeRelay(record)
    relay.mode = "login"
    result, brain, relay, model = run_brain(
        tmp_path,
        record,
        [
            decision(
                kind="login", intent="credential", action=text_choice("credential.password", "e8")
            ),
            decision("stop", intent="stop", kind="login"),
        ],
        relay=relay,
        password="sensitive-fake-password",
    )
    assert result.reason == "model_requested_stop"
    assert relay.actions[0].root.text == "sensitive-fake-password"
    assert "sensitive-fake-password" not in brain.log.path.read_text()
    assert all("sensitive-fake-password" not in prompt for prompt in model.prompts)
    assert all(
        "sensitive-fake-password" not in path.read_text()
        for path in (tmp_path / "observations").glob("*.json")
    )


def test_summary_preserves_grid_relationships_and_omits_ids(record):
    observation = FakeRelay(record, duplicate=True).observe()
    original = observation.elements[-1]
    observation.elements[-1] = original.model_copy(update={"automation_id": "private-id"})
    for strategy in ("flat_fields", "label_grouped"):
        summary = json.loads(summarize(observation, strategy))
        cell = next(item for item in summary["elements"] if item["ref"] == original.ref)
        assert cell["parent_ref"] == "e6"
        assert cell["value"] == original.value
        assert "private-id" not in json.dumps(summary)
    grouped = json.loads(summarize(observation, "label_grouped"))
    assert grouped["elements"][-1]["region"][-1]["ref"] == "e6"


def test_snapshot_redaction_scrubs_all_ui_strings(record):
    observation = FakeRelay(record).observe()
    secret = "TOKEN"
    for field in ("name", "label_hint", "value", "automation_id"):
        setattr(observation.elements[1], field, f"{field}: {secret}")
    observation.active_window = observation.active_window.model_copy(
        update={"title": f"active title {secret}", "process": f"active process {secret}"}
    )
    observation.windows = [
        observation.active_window.model_copy(),
        observation.windows[0].model_copy(
            update={
                "handle": 2,
                "title": f"listed title {secret}",
                "process": f"listed process {secret}",
            }
        ),
    ]
    observation.blocked_reason = f"blocked {secret}"
    redacted = redact(observation, (secret,))
    assert secret not in redacted.model_dump_json()
    for field in ("name", "label_hint", "value", "automation_id"):
        assert getattr(redacted.elements[1], field) == f"{field}: [REDACTED]"
    assert redacted.active_window.title == "active title [REDACTED]"
    assert redacted.active_window.process == "active process [REDACTED]"
    assert redacted.windows[0] == redacted.active_window
    assert redacted.windows[1].title == "listed title [REDACTED]"
    assert redacted.windows[1].process == "listed process [REDACTED]"
    assert redacted.blocked_reason == "blocked [REDACTED]"
    assert observation.elements[1].value == "value: TOKEN"


def test_short_password_redaction_preserves_protocol_identity(record):
    observation = FakeRelay(record).observe()
    observation.elements[1].value = "e1"
    redacted = redact(observation, ("e1", "0.2"))
    assert redacted.obs_id == observation.obs_id
    assert redacted.schema_version == observation.schema_version
    assert redacted.elements[1].ref == "e1"
    assert redacted.elements[1].value == "[REDACTED]"


@pytest.mark.parametrize(
    "field,actual,expected",
    [
        ("hbl_no", " test-a01 ", True),
        ("gross_weight_kg", "1,250.50", True),
        ("gross_weight_kg", "12,50.5", False),
        ("gross_weight_kg", "1250.500000001", False),
        ("packages.count", "12 CT", True),
        ("packages.unit", "12 CT", True),
        ("packages.count", "13 CT", False),
    ],
)
def test_exact_field_comparison(record, field, actual, expected):
    assert field_matches(field, actual, values_for(record)) is expected


@pytest.mark.parametrize("url", ["file:///secret", "http://u:p@localhost", "http://localhost/path"])
def test_http_boundary_rejects_invalid_origins(url):
    with pytest.raises(ValueError):
        HttpRelay(url, "token")


def test_decision_schema_has_no_freeform_text_or_coordinate_action():
    schema = Decision.model_json_schema()
    choice = schema["$defs"]["Choice"]["properties"]
    assert "text" not in choice
    assert "point" not in choice
    assert schema["$defs"]["Choice"]["additionalProperties"] is False
    base = decision(intent="query_input", action=text_choice("hbl_no", "e1"))
    assert Decision.model_validate(base).action.source == "hbl_no"
    for key, value in {"text": "untrusted literal", "point": [1, 2], "image_point": [1, 2]}.items():
        with pytest.raises(ValidationError) as error:
            Decision.model_validate({**base, "action": {**base["action"], key: value}})
        assert [(e["loc"], e["type"]) for e in error.value.errors()] == [
            (("action", key), "extra_forbidden")
        ]


def test_wait_cannot_be_claimed_as_executed_command(tmp_path, record):
    invalid = decision(intent="search", action={"type": "wait", "duration_ms": 0})
    result, brain, relay, model = run_brain(
        tmp_path,
        record,
        [query_steps()[0], invalid, invalid],
    )
    assert result.reason == "repeated_subgoal_failure:duplicate_query:decision"
    assert result.steps == 3
    assert len(model.prompts) == 4
    failures = [
        event.payload
        for event in brain.log.read_all()
        if event.kind == "verify" and event.payload.get("ok") is False
    ]
    assert [item["reason"] for item in failures] == [
        "selection_error:ValidationError",
        "selection_error:ValidationError",
    ]
    assert [a.root.type for a in relay.actions] == ["set_text"]
    assert brain.query_value_verified
    assert not brain.query_submitted and not brain.save_issued
    assert brain._decision_policy().allows(Decision.model_validate(query_steps()[1]))
    with pytest.raises(GuardStop, match="^action_not_allowed_by_state$"):
        brain._guard_action(Decision.model_validate(invalid))


def test_cli_setup_failure_has_durable_result_and_never_reuses_case(tmp_path, record, monkeypatch):
    record_path = tmp_path / "record.json"
    record_path.write_text(record.model_dump_json(), encoding="utf-8")
    config = tmp_path / "brain.toml"
    config.write_text('schema_version = "0.1"\n', encoding="utf-8")
    case_dir = tmp_path / "new-case"
    monkeypatch.setenv("SAWON_RELAY_TOKEN", "fake-token")
    args = [
        "--record",
        str(record_path),
        "--case-dir",
        str(case_dir),
        "--relay-url",
        "http://127.0.0.1:1",
        "--goal",
        "Synthetic task",
        "--config",
        str(config),
        "--model-config",
        str(tmp_path / "missing.toml"),
    ]
    assert main(args) == 2
    output = (case_dir / "result.json").read_bytes()
    log = (case_dir / "run_log.jsonl").read_bytes()
    assert json.loads(output)["reason"] == "setup_error:FileNotFoundError"
    assert StateStore(case_dir, case_id=record.case_id).read().status == "failed"
    assert main(args) == 2
    assert (case_dir / "result.json").read_bytes() == output
    assert (case_dir / "run_log.jsonl").read_bytes() == log


def test_rotating_failed_intents_and_successful_wait_do_not_reset_limit(tmp_path, record):
    class FailingActions(FakeRelay):
        def act(self, action):
            if action.root.type == "wait":
                return super().act(action)
            self.actions.append(action)
            return ActionResult(
                action_id=action.root.action_id,
                ok=False,
                error="fixture rejection",
                duration_ms=0.0,
            )

    result, _, relay, _ = run_brain(
        tmp_path,
        record,
        [
            decision(intent="query_input", action=text_choice("hbl_no", "e1")),
            decision(intent="wait", action={"type": "wait", "duration_ms": 0}),
            decision(intent="navigate", action={"type": "click", "ref": "e3"}),
        ],
        relay=FailingActions(record),
    )
    assert result.reason == "repeated_phase_failure:duplicate_query"
    assert len(relay.actions) == 3


class StructuralRowRelay(FakeRelay):
    def __init__(self, record, *, row_role="Custom"):
        super().__init__(record)
        self.row_role = row_role

    def observe(self):
        observation = super().observe()
        if self.saved:
            next(item for item in observation.elements if item.ref == "e6").role = self.row_role
        observation.elements.extend(
            [
                element("e70", "Custom", parent="e4" if self.mode == "query" else "e0"),
                element("e71", "Header", name="Column heading", parent="e70"),
            ]
        )
        return observation


@pytest.mark.parametrize("shape", ["empty", "headers", "mixed", "nested_rows", "outside_grid"])
def test_generic_container_requires_unambiguous_direct_grid_cells(shape):
    items = [
        element("e0", "Window"),
        element("e1", "DataGrid", parent="e0"),
        element("e2", "Custom", parent="e0" if shape == "outside_grid" else "e1"),
    ]
    if shape != "empty":
        items.append(element("e3", "Header" if shape == "headers" else "DataItem", parent="e2"))
    if shape == "mixed":
        items.append(element("e4", "Button", parent="e2"))
    elif shape == "nested_rows":
        items.append(element("e4", "DataItem", parent="e3"))
    assert "e2" not in row_refs(items)


@pytest.mark.parametrize("strategy", ["flat_fields", "label_grouped"])
def test_nested_grid_summary_uses_nearest_row_context(record, strategy):
    observation = FakeRelay(record, duplicate=True).observe()
    observation.elements.extend(
        [
            element("e80", "Table", parent="e6"),
            element("e81", "Custom", parent="e80"),
            element("e82", "DataItem", value="Nested cell", parent="e81"),
        ]
    )
    assert row_refs(observation.elements, grid_ref="e4") == {"e6"}
    assert row_refs(observation.elements, grid_ref="e80") == {"e81"}
    summary = json.loads(summarize(observation, strategy))
    by_ref = {item["ref"]: item for item in summary["elements"]}
    assert by_ref["e82"]["row_ref"] == "e81"
    assert by_ref["e81"]["row_ref"] == "e81"
    assert by_ref["e40"]["row_ref"] == "e6"


def test_observed_call_exceeding_cost_cap_stops_before_ui_work(tmp_path, record):
    result, _, relay, model = run_brain(tmp_path, record, [], price=Decimal("0.51"))
    assert result.reason == "case_cost_cap"
    assert len(model.prompts) == 1
    assert not relay.actions
