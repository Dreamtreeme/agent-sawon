"""Synthetic negative controls for the mixed-inbox effects oracle."""

import json
from copy import deepcopy

import pytest
from grade import FIXTURES, check_effects, load


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("wrong_shipment", "unchanged A row differs"),
        ("duplicate_shipment", "expected one, found 2"),
        ("reverted_extra_save", "unexpected writes"),
    ],
)
def test_effects_oracle_rejects_unsafe_writes(fault, reason):
    gold = load(FIXTURES / "grading/expected.json")

    def row(row_id, hbl, quantity):
        data = deepcopy(gold["create"])
        data["hbl_no"] = hbl
        data["packages"]["count"] = quantity
        return {"id": row_id, "state": "saved", "transmitted": 0, "data_json": json.dumps(data)}

    def event(kind, row_id):
        return {"event_type": kind, "shipment_id": row_id, "allowed": 1}

    a, b, c = (gold[k]["hbl_no"] for k in ("untouched", "amend", "create"))
    before = {1: row(1, a, 30), 2: row(2, b, 18)}
    after = {1: before[1], 2: row(2, b, 22), 3: row(3, c, 18)}
    events = {1: event("shipment_updated", 2), 2: event("shipment_created", 3)}
    assert check_effects(before, after, {}, events, gold)["saved_ids"] == [1, 2, 3]

    if fault == "wrong_shipment":
        after.update({1: row(1, a, 22), 2: before[2]})
        events[1] = event("shipment_updated", 1)
    elif fault == "duplicate_shipment":
        after[4] = row(4, c, 18)
        events[3] = event("shipment_created", 4)
    else:
        events[3] = event("shipment_updated", 1)
    with pytest.raises(ValueError, match=reason):
        check_effects(before, after, {}, events, gold)
