"""Current screen proposals use local references and fresh capture geometry."""

from copy import deepcopy

import pytest
from test_brain import decision, text_choice
from test_brain import record as record

from brain.graph.visual_turn import adapt_proposal, bind_proposal
from brain.visual.scene import SemanticDetection


def test_current_policy_rejects_wrong_phase_action_and_unknown_marker():
    from brain.graph.policy import DecisionPolicy, ExecutionFacts
    from brain.graph.schemas import DecisionResponse

    proposal = DecisionResponse.model_validate(
        {"decision": decision(intent="query_input", action=text_choice("hbl_no", "m5"))}
    ).model_dump(mode="json")
    original = deepcopy(proposal)
    with pytest.raises(ValueError, match="unknown_marker"):
        bind_proposal(proposal, "e10", {"e106"})
    bound = bind_proposal(proposal, "e10", {"e105"})
    assert bound["decision"]["action"]["ref"] == "e105"
    assert proposal == original
    policy = DecisionPolicy(
        ExecutionFacts(phase="enter", duplicate_clear=True, field_names=("hbl_no",))
    )
    with pytest.raises(ValueError):
        adapt_proposal(bound, policy.response_schema())
    # Metadata does not turn local semantic detection into a persistence claim.
    assert "next_step" not in SemanticDetection.model_fields


@pytest.mark.parametrize("change", ["origin", "dpi", "size", "content"])
def test_capture_renewal_keeps_exact_points_and_locally_rebinds_changed_content(
    tmp_path, record, change
):
    from test_screen_batch import ChangingPixelsRelay, make_brain

    from brain.graph.schemas import Decision

    relay = ChangingPixelsRelay(record)
    brain, perception, model = make_brain(tmp_path, record, relay, [])
    previous = brain._observe()
    original_observe = relay.observe

    def changed_capture():
        observed = original_observe()
        shot = observed.screenshot
        geometry = shot.geometry
        if change == "origin":
            geometry = geometry.model_copy(update={"origin": (200, 300)})
        elif change == "dpi":
            geometry = geometry.model_copy(update={"dpi": 192})
        elif change == "size":
            geometry = geometry.model_copy(update={"physical_size": (2000, 200)})
        shot = shot.model_copy(update={"geometry": geometry})
        updated = {"screenshot": shot}
        if change == "content":
            updated["screenshot"] = shot.model_copy(update={"content_hash": "c" * 40})
            updated["screen_hash"] = "c" * 40
        relay.current = observed.model_copy(update=updated)
        return relay.current

    relay.observe = changed_capture
    selected = Decision.model_validate(
        decision(intent="query_input", action=text_choice("hbl_no", "e1"))
    )
    action = brain._wire_action(selected.action, selected)
    assert action.root.obs_id != previous.obs_id
    assert action.root.target.capture_id == relay.current.screenshot.geometry.capture_id
    assert action.root.target.image_point == (14, 20)
    if change == "origin":
        assert relay.current.screenshot.desktop_point(action.root.target.image_point) == (
            214,
            320,
        )
    assert len(perception.action_counts) == 1
    assert not model.prompts  # Geometry renewal never invokes the semantic model.
