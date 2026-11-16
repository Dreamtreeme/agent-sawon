"""3계층 구조화 스크래치패드 및 롤링 턴 버퍼 동작 검증 단위 테스트."""

from copy import deepcopy

from test_brain import FakeRelay
from test_brain import record as record
from test_screen_decision import setup_brain

from brain.graph.schemas import Choice, Decision
from brain.graph.scratchpad import RollingContextManager
from brain.graph.screen_mapping import ScreenMapping
from brain.graph.screen_memory import remember_input_delivery, remember_screen
from brain.graph.transitions import ObservedEffect


def test_rolling_buffer_strictly_bounds_turn_window():
    """버퍼 크기(3)를 초과해 기록하면 오래된 턴이 자동 퇴출되고 최신 3턴만 유지되는지 검증."""
    manager = RollingContextManager(buffer_size=3)

    # 5개 턴을 순차적으로 기록
    for i in range(1, 6):
        manager.record_turn(
            turn_index=i,
            intent="fill" if i % 2 == 0 else "navigate",
            target_summary=f"field_{i}",
            action_type="set_text",
            observed_effect="input_value_matches",
            checkpoint="fill_fields",
        )

    # 1, 2턴은 퇴출되고 최신 3개 턴(3, 4, 5)만 남아있어야 함
    turns = manager.recent_turns
    assert len(turns) == 3
    assert [t.turn_index for t in turns] == [3, 4, 5]
    assert [t.target_summary for t in turns] == ["field_3", "field_4", "field_5"]


def test_recorded_facts_survive_eviction_without_rewording():
    """과거 턴이 밀려나도 보고된 사실을 완료 선언으로 바꾸거나 잘라내지 않는다."""
    manager = RollingContextManager(buffer_size=2)

    manager.record_fact("credential.username_submitted")
    manager.record_turn(
        turn_index=1,
        intent="login",
        target_summary="btn_login",
        action_type="click",
        observed_effect="window_changed",
    )

    manager.record_fact("query_submitted")
    manager.record_turn(
        turn_index=2,
        intent="search",
        target_summary="btn_search",
        action_type="click",
        observed_effect="query_results_displayed",
    )

    # 턴 3: 첫 번째 필드 입력 (버퍼 크기가 2이므로 턴 1은 롤링 버퍼에서 소멸)
    manager.record_turn(
        turn_index=3,
        intent="fill",
        target_summary="input_hbl",
        action_type="set_text",
        observed_effect="input_value_matches",
    )

    payload = manager.export_prompt_context(
        current_goal="선적 서류 상세 입력",
    )

    # 롤링 버퍼에는 2, 3턴만 남아 있음
    assert [t["turn_index"] for t in payload["recent_turns_buffer"]] == [2, 3]

    assert payload["scratchpad"]["recorded_facts"] == [
        "credential.username_submitted",
        "query_submitted",
    ]
    assert payload["scratchpad"]["current_goal"] == "선적 서류 상세 입력"
    assert "verified_inputs" not in payload["scratchpad"]
    assert "remaining_inputs" not in payload["scratchpad"]
    long_source = "document." + "reference" * 20
    manager.record_fact(long_source)
    manager.record_fact(long_source)
    assert manager.recorded_facts == [
        "credential.username_submitted",
        "query_submitted",
        long_source,
    ]


def test_synthetic_engine_summary_preserves_delivery_effect_and_input_facts(tmp_path, record):
    # Synthetic state; the real engine recorder must not promote a receipt or change into proof.
    brain, *_ = setup_brain(tmp_path, record, [])
    relay = FakeRelay(record)
    relay.mode = "editor"
    before = relay.observe()
    relay.mode = "query"
    brain.observation = relay.observe()
    brain.pending = {"before_observation": before}
    brain.current_screen_path = ["Synthetic entry"]
    mapping = ScreenMapping.model_validate(
        {
            "screen_path": brain.current_screen_path,
            "fields": {
                "Reference": {
                    "role": "edit",
                    "source": "hbl_no",
                    "bbox": [10, 10, 100, 30],
                    "value": brain.input_values["hbl_no"],
                }
            },
        }
    )
    remember_screen(brain.screen_nodes, mapping, brain.input_values, "read-1", observation_number=1)
    remember_input_delivery(
        brain.screen_nodes,
        brain.current_screen_path,
        "mbl_no",
        brain.input_values["mbl_no"],
        target={"label": "Master reference", "role": "edit", "bbox": [10, 50, 100, 30]},
        observation_id="delivery-2",
        observation_number=2,
    )
    original_nodes = deepcopy(brain.screen_nodes)
    original_values = dict(brain.input_values)
    original_verified = deepcopy(brain._observed_inputs())
    cases = [
        ("save", Choice(type="click", ref="e7"), False, ObservedEffect.NO_CHANGE, ["save_issued"]),
        (
            "navigate",
            Choice(type="focus_window", handle=before.active_window.handle),
            True,
            ObservedEffect.CHANGED,
            [],
        ),
    ]
    for intent, choice, navigation_change, effect, facts in cases:
        decision = Decision(
            screen_kind="editor",
            operation="act",
            intent=intent,
            subgoal="Synthetic summary boundary",
            reason="Record the measured outcome without inventing confirmation",
            action=choice,
        )
        brain._record_action_verification(decision, choice, None, navigation_change, effect, facts)
        payload = brain.rolling_context.export_prompt_context(current_goal=brain.goal)
        turn = payload["recent_turns_buffer"][-1]
        assert turn["observed_effect"] == str(effect), intent
        assert "success" not in turn, intent
        assert payload["scratchpad"]["recorded_facts"] == ["save_issued"], intent
        assert "completed_milestones" not in payload["scratchpad"], intent
        assert brain.screen_nodes == original_nodes, intent
        assert brain.input_values == original_values, intent
        assert brain._observed_inputs() == original_verified, intent
    assert payload["recent_turns_buffer"][0]["target_summary"] == "click:Persist object"
    assert set(original_verified) == {"hbl_no"}
