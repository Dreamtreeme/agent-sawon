"""Completion eligibility obeys the final proof's current-cell uniqueness rule."""

from test_brain import FakeRelay, field_matches, values_for
from test_brain import record as record

from brain.graph.review import _assign_fields, review_final


def test_equal_party_values_require_two_current_cells(record):
    record.fields.consignee.value = record.fields.shipper.value
    observation = FakeRelay(record, duplicate=True).observe()
    without_consignee = observation.model_copy(
        update={"elements": [item for item in observation.elements if item.ref != "e43"]}
    )
    report = review_final(without_consignee, values_for(record), field_matches)
    assert report.status == "needs_more"
    assert set(report.missing_fields) <= {"shipper", "consignee"}
    assert len(report.missing_fields) == 1
    assert review_final(observation, values_for(record), field_matches).status == "ready"


def test_combined_package_cell_is_the_only_shared_exception(record):
    observation = FakeRelay(record, duplicate=True).observe()
    elements = [item for item in observation.elements if item.ref != "e46"]
    elements = [
        item.model_copy(update={"value": "12 CT"}) if item.ref == "e45" else item
        for item in elements
    ]
    combined = observation.model_copy(update={"elements": elements})
    report = review_final(combined, values_for(record), field_matches)
    assert report.status == "ready"
    assert report.missing_fields == ()


def test_package_shared_cell_cannot_cover_a_third_field(record):
    record.fields.description.value = "12 CT"
    observation = FakeRelay(record, duplicate=True).observe()
    elements = [item for item in observation.elements if item.ref not in {"e44", "e46"}]
    elements = [
        item.model_copy(update={"value": "12 CT"}) if item.ref == "e45" else item
        for item in elements
    ]
    report = review_final(
        observation.model_copy(update={"elements": elements}), values_for(record), field_matches
    )
    assert report.status == "needs_more"
    assert report.missing_fields


def test_augmenting_path_finds_assignment_that_greedy_first_fit_misses():
    assignment = _assign_fields({"a": {"e1", "e2"}, "b": {"e1", "e3"}, "c": {"e1", "e3"}})
    assert set(assignment) == {"a", "b", "c"}
    assert len(set(assignment.values())) == 3
