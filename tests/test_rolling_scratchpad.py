"""3계층 구조화 스크래치패드 및 롤링 턴 버퍼 동작 검증 단위 테스트."""

from brain.graph.scratchpad import RollingContextManager


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
            success=True,
            checkpoint="fill_fields",
        )

    # 1, 2턴은 퇴출되고 최신 3개 턴(3, 4, 5)만 남아있어야 함
    turns = manager.recent_turns
    assert len(turns) == 3
    assert [t.turn_index for t in turns] == [3, 4, 5]
    assert [t.target_summary for t in turns] == ["field_3", "field_4", "field_5"]


def test_persistent_milestones_survive_eviction():
    """오래된 턴이 버퍼에서 밀려나도 Layer 1 마일스톤 성과는 영구 보존되는지 검증."""
    manager = RollingContextManager(buffer_size=2)

    # 턴 1: 로그인 마일스톤 달성
    manager.mark_milestone("로그인 성공")
    manager.record_turn(
        turn_index=1,
        intent="login",
        target_summary="btn_login",
        action_type="click",
        observed_effect="window_changed",
        success=True,
    )

    # 턴 2: 중복 조회 완료 마일스톤 달성
    manager.mark_milestone("중복 조회 확인: 결과 0건")
    manager.record_turn(
        turn_index=2,
        intent="search",
        target_summary="btn_search",
        action_type="click",
        observed_effect="query_results_displayed",
        success=True,
    )

    # 턴 3: 첫 번째 필드 입력 (버퍼 크기가 2이므로 턴 1은 롤링 버퍼에서 소멸)
    manager.record_turn(
        turn_index=3,
        intent="fill",
        target_summary="input_hbl",
        action_type="set_text",
        observed_effect="input_value_matches",
        success=True,
    )

    payload = manager.export_prompt_context(
        current_goal="선적 서류 상세 입력",
        verified_inputs=["hbl_no"],
        remaining_inputs=["mbl_no", "shipper"],
    )

    # 롤링 버퍼에는 2, 3턴만 남아 있음
    assert [t["turn_index"] for t in payload["recent_turns_buffer"]] == [2, 3]

    # 하지만 Layer 1 스크래치패드에는 턴 1의 성과가 유실 없이 보존됨
    assert payload["scratchpad"]["completed_milestones"] == [
        "로그인 성공",
        "중복 조회 확인: 결과 0건",
    ]
    assert payload["scratchpad"]["current_goal"] == "선적 서류 상세 입력"
    assert payload["scratchpad"]["verified_inputs"] == ["hbl_no"]