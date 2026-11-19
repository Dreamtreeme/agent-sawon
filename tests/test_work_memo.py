from copy import deepcopy

from brain.graph.work_memo import memo_prompt_context


def test_memo_item_names_follow_the_same_input_across_teaching_history_and_recovery():
    context = {
        "notebook": {
            "inputs": {"pol": "BUSAN", "description": "pol"},
            "remaining_inputs": ["pol", "description"],
            "available_credentials": ["credential.username"],
            "screen_tree": {
                "children": {
                    "pol": {
                        "children": {},
                        "fields": {"pol": {"label": "pol", "last_observation": {"value": "pol"}}},
                    }
                },
                "fields": {},
            },
            "recent_turns": [{"action_type": "set_text", "target_summary": "set_text:pol"}],
            "saved_record_review": {
                "identity_field": "pol",
                "evidence": {"pol": {"value": "pol"}},
            },
        },
        "task": {"identity_input_key": "pol", "input_formats": {"pol": {"unit": "pol"}}},
        "screen": {"text_regions": [{"text": "pol"}]},
        "execution": {"last_delivered_actions": [{"source": "pol", "target": "pol"}]},
        "recovery": {
            "required_sources": ["pol"],
            "current_observation": {"fields": {"pol": {"source": "pol", "value": "pol"}}},
        },
        "taught_procedure_progress": {
            "steps": [{"actions": [{"target": "pol", "input_key": "pol"}]}],
        },
    }
    original = deepcopy(context)
    labels = {"pol": "선적항", "description": "품명", "credential.username": "로그인 계정"}
    view = memo_prompt_context(context, labels, "선적 의뢰")
    assert context == original
    notebook = view["notebook"]
    assert "inputs" not in notebook
    assert notebook["work_memo"] == {
        "name": "선적 의뢰",
        "items": {"선적항": "BUSAN", "품명": "pol"},
    }
    assert notebook["remaining_inputs"] == ["선적항", "품명"]
    assert notebook["available_credentials"] == ["로그인 계정"]
    assert notebook["screen_tree"]["children"]["pol"]["fields"] == {
        "선적항": {"label": "pol", "last_observation": {"value": "pol"}}
    }
    assert notebook["recent_turns"][0]["target_summary"] == "set_text:선적항"
    assert notebook["saved_record_review"]["evidence"] == {"선적항": {"value": "pol"}}
    assert view["task"] == {
        "identity_memo_item": "선적항",
        "input_formats": {"선적항": {"unit": "pol"}},
    }
    assert view["screen"] == original["screen"]
    assert view["execution"]["last_delivered_actions"] == [{"source": "선적항", "target": "pol"}]
    assert view["recovery"]["required_sources"] == ["선적항"]
    assert view["recovery"]["current_observation"]["fields"] == {
        "pol": {"source": "선적항", "value": "pol"}
    }
    assert view["taught_procedure_progress"]["steps"][0]["actions"] == [
        {"target": "pol", "memo_item": "선적항"}
    ]


def test_original_readings_keep_their_keys_when_a_memo_also_supplies_the_same_business_field():
    links = {"quantity": "예정 수량", "collected_result.quantity": "실제 수량"}
    observation = {"fields": {"메일 원문": {"source": "quantity", "value": "43"}}}
    context = {
        "task": {"result_keys": ["quantity"]},
        "notebook": {
            "inputs": {"quantity": "40", "collected_result.quantity": "43"},
            "collected_result": {"values": {"quantity": "43"}, "provenance": {"quantity": [1]}},
            "screen_tree": {"fields": {"quantity": {"label": "메일 수량"}}, "children": {}},
            "available_credentials": [],
        },
        "recovery": {"current_observation": observation},
    }
    view = memo_prompt_context(context, {**links, "credential.password": "비밀번호"}, "정정")
    assert view["task"]["result_keys"] == ["quantity"]
    assert view["notebook"]["work_memo"]["items"] == {"예정 수량": "40", "실제 수량": "43"}
    assert view["notebook"]["collected_result"] == context["notebook"]["collected_result"]
    assert view["notebook"]["screen_tree"] == context["notebook"]["screen_tree"]
    assert view["notebook"]["available_credentials"] == []
    assert view["recovery"]["current_observation"] == observation
