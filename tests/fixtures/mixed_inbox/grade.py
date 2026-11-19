"""Read-only independent scoring of the three-message office scenario (see RUBRIC.md)."""

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime
from decimal import Decimal, InvalidOperation
from email import policy
from email.parser import BytesParser
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_bytes())


def table(path, name, key="id"):
    with closing(sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return {r[key]: dict(r) for r in db.execute(f"SELECT * FROM {name}")}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def one(items, description):
    rows = list(items)
    require(len(rows) == 1, f"{description}: expected one, found {len(rows)}")
    return rows[0]


def fields(data):
    return {
        **{k: v for k, v in data.items() if k not in ("packages", "items")},
        "packages.count": data["packages"]["count"],
        "packages.unit": data["packages"]["unit"],
    }


def same_value(key, actual, expected):
    if key in ("packages.count", "gross_weight_kg"):
        try:
            text = str(actual).strip().upper()
            if key == "gross_weight_kg":
                text = text.removesuffix("KG").strip()
            else:
                text = text.removesuffix("CTNS").strip()
            return Decimal(text) == Decimal(str(expected))
        except InvalidOperation:
            return False
    return actual == expected


def find_row(rows, hbl):
    return one((r for r in rows.values() if json.loads(r["data_json"])["hbl_no"] == hbl), hbl)


def writes(events):
    return Counter(
        (e["event_type"], e["shipment_id"], e["allowed"])
        for e in events
        if e["event_type"] not in ("login", "shipment_lookup")
    )


def check_effects(before, after, prior_events, events, expected):
    a = find_row(before, expected["untouched"]["hbl_no"])
    b = find_row(before, expected["amend"]["hbl_no"])
    c = find_row(after, expected["create"]["hbl_no"])
    require(len(before) == 2 and len(after) == 3 and c["id"] not in before, "row count or C ID")
    require(after.get(a["id"]) == a, "unchanged A row differs")
    updated = after[b["id"]]
    old_data = json.loads(b["data_json"])
    old_data["packages"]["count"] = expected["amend"]["after_count"]
    require(json.loads(updated["data_json"]) == old_data, "B differs outside the requested change")
    require(all(updated[k] == b[k] for k in b if k not in {"data_json", "updated_at"}), "B ID/meta")
    c_data = json.loads(c["data_json"])
    require(fields(c_data) == fields(expected["create"]), "new shipment fields differ")
    require(
        c_data.get("items", []) == [] and c["state"] == "saved" and not c["transmitted"], "C state"
    )
    require(all(events.get(k) == v for k, v in prior_events.items()), "old audit events changed")
    new = [v for k, v in events.items() if k not in prior_events]
    wanted = Counter([("shipment_created", c["id"], 1), ("shipment_updated", b["id"], 1)])
    require(writes(new) == wanted, f"unexpected writes: {dict(writes(new))}")
    return {"saved_ids": [a["id"], b["id"], c["id"]], "writes": [list(w) for w in wanted]}


def compact(s):
    return "".join(s.casefold().split())


def requery(case, hbl, quantity, row_id, since):
    """Bind the one-result grid and the subsequently reopened same-ID detail to this run."""
    grid = None
    detail = {}
    for snap in sorted((case / "observations").glob("*.json")):
        obs = load(snap)
        wire = obs.get("wire", obs)
        title = compact((wire.get("active_window") or {}).get("title", ""))
        shot = (wire.get("screenshot") or {}).get("geometry") or {}
        ts = shot.get("captured_at") or wire.get("ts")
        visual = case / "visual" / str(wire["obs_id"])
        png, ocr = visual / "capture.png", visual / "ocr.json"
        if not ts or datetime.fromisoformat(ts) < since or not (png.is_file() and ocr.is_file()):
            continue
        observed = load(ocr)
        if observed.get("obs_id") != wire["obs_id"]:
            continue
        texts = [compact(b["text"]) for b in observed["blocks"]]
        if "houseb/l조회" in title and compact(hbl) in texts and "조회결과1건" in texts:
            grid = str(png)
            detail.clear()
        if grid and "houseb/l수정" in title:
            if any(re.search(rf"저장번호[:：]?{row_id}(?!\d)", t) for t in texts):
                for key, value in (("hbl", compact(hbl)), ("quantity", str(quantity))):
                    if value in texts:
                        detail[key] = str(png)
                if len(detail) == 2:
                    return {"grid": grid, "detail": detail}
    raise ValueError(f"post-save single result and reopened detail missing for {hbl}")


class Audit:
    def __init__(self, run, daily, oracle):
        self.run, self.daily, self.gold = run, daily, oracle
        self.old_db = run / "mixed-before.db"
        self.db = run / "erp.db"
        self.ib = run / "state/inbox.db"
        self.old_ib = run / "inbox-before.db"

    def initial(self):
        snapshot = table(self.old_db, "shipments")
        require(len(snapshot) == 2, "initial ERP should contain exactly A+B")
        for section in ("untouched", "amend"):
            target = self.gold[section]
            row = find_row(snapshot, target["hbl_no"])
            f = fields(json.loads(row["data_json"]))
            require(row["state"] == "saved" and not row["transmitted"], "row not open for edit")
            require(f["packages.count"] == target["before_count"], "initial count")
            require(f["shipper"] == self.gold["create"]["shipper"], "different account")
        digest = hashlib.sha256(self.daily.read_bytes()).hexdigest()
        require(
            digest
            == (self.run / "daily-before.sha256").read_text(encoding="utf-8-sig").strip().lower(),
            "daily DB changed",
        )
        return {"initial_row_ids": list(snapshot), "daily_sha256": digest}

    def arrivals(self):
        receipts = table(self.ib, "receipts")
        original = table(self.old_ib, "receipts")
        new_rows = [r for k, r in receipts.items() if k not in original]
        require(len(new_rows) == 3, f"arrivals={len(new_rows)}")
        messages = self.gold["messages"]
        result = {}
        for role, value in messages.items():

            def matches(r, role=role, value=value):
                if role == "new_subject":
                    return (
                        r["source"] == "email"
                        and str(BytesParser(policy=policy.default).parsebytes(r["raw"])["Subject"])
                        == value
                    )
                return (
                    r["source"] == "kakao_message"
                    and r["new_text"].partition("\n")[2].strip() == value
                )

            result[role] = one(filter(matches, new_rows), role)
        return result

    def plan(self, role):
        receipt = self.arrivals()[role]
        plan_id = receipt["plan_id"]
        require(plan_id is not None, f"{role} was not assigned to a plan")
        linked = [r["id"] for r in table(self.ib, "receipts").values() if r["plan_id"] == plan_id]
        require(linked == [receipt["id"]], f"{role} was pooled with other messages {linked}")
        return plan_id, json.loads(table(self.ib, "plans")[plan_id]["decision"])

    def new(self):
        old = table(self.old_ib, "plans")
        pid, decision = self.plan("new_subject")
        previous = {json.loads(p["decision"]).get("work_id") for p in old.values()}
        require(
            pid not in old and not decision.get("supersedes_plan_id"),
            "new request revised another plan",
        )
        require(decision["work_id"] and decision["work_id"] not in previous, "reused work identity")
        return {"new_plan_id": pid, "work_id": decision["work_id"]}

    def amend(self):
        pid, d = self.plan("correction_body")
        old = table(self.old_ib, "plans")
        parent_id = d.get("supersedes_plan_id")
        require(
            parent_id in old
            and old[parent_id]["stage"] == "exited"
            and old[parent_id]["worker_exit"] == 0,
            "missing successful previous plan",
        )
        require(
            all(
                json.loads(p["decision"]).get("supersedes_plan_id") != parent_id
                for p in old.values()
            ),
            "B plan already had a newer revision",
        )
        parent = json.loads(old[parent_id]["decision"])
        require(d["work_id"] == parent["work_id"], "different work identity")
        history = {f["key"]: f for f in parent["facts"]}
        require(
            any(f["value"] == self.gold["amend"]["hbl_no"] for f in history.values()),
            "parent is not B",
        )
        receipt = self.arrivals()["correction_body"]["id"]
        changed = [f for f in d["facts"] if f != history.get(f["key"])]
        original_keys = {f["key"] for f in d["facts"]}
        require(set(history) <= original_keys, "revision dropped inherited facts")
        old_changes = [f for f in changed if f["key"] in history]
        q = one(old_changes, "changed existing key on B")
        before_quantity = history[q["key"]]["value"]
        require(
            same_value("packages.count", before_quantity, self.gold["amend"]["before_count"]),
            "different original quantity",
        )
        require(
            q["receipt_id"] == receipt
            and same_value("packages.count", q["value"], self.gold["amend"]["after_count"]),
            "wrong quantity/source",
        )
        return {"parent_id": parent_id, "plan_id": pid, "work_id": d["work_id"], "quantity_fact": q}

    def reference(self):
        arrivals = self.arrivals()
        ref = arrivals["reference_body"]
        refs = table(self.ib, "inbox_references", "receipt_id")
        require(ref["id"] in refs and ref["plan_id"] is None, "reference not filed independently")
        old_refs = table(self.old_ib, "inbox_references", "receipt_id")
        require(set(refs) - set(old_refs) == {ref["id"]}, "unexpected extra references")
        pending = [
            r["id"]
            for r in table(self.ib, "receipts").values()
            if r["kind"] != "baseline" and r["plan_id"] is None and r["id"] not in refs
        ]
        require(not pending, f"unhandled receipts: {pending}")
        for name, key in (("plans", "id"), ("receipts", "id"), ("inbox_references", "receipt_id")):
            old, current = table(self.old_ib, name, key), table(self.ib, name, key)
            require(all(current.get(k) == v for k, v in old.items()), f"{name} history changed")
        plans = set(table(self.ib, "plans")) - set(table(self.old_ib, "plans"))
        assigned = {arrivals[r]["plan_id"] for r in ("new_subject", "correction_body")}
        require(len(plans) == 2 and plans == assigned, "extra, missing or pooled plan")
        return {"reference_receipt_id": ref["id"], "actionable_plan_ids": sorted(plans)}

    def worker(self, plan_id):
        runs = []
        for f in (self.run / "runs").glob("*/trial-result.json"):
            outcome = load(f)
            if outcome.get("plan_id") == plan_id:
                runs.append((f.parent / "worker", outcome))
        if not runs:
            raise FileNotFoundError(f"worker run for plan {plan_id}")
        return one(runs, f"dispatches for plan {plan_id}")

    def dispatch(self):
        receipts = self.arrivals()
        for role in ("new_subject", "correction_body"):
            pid, decision = self.plan(role)
            case, outcome = self.worker(pid)
            expected = (
                fields(self.gold["create"])
                if role == "new_subject"
                else {
                    "hbl_no": self.gold["amend"]["hbl_no"],
                    "packages.count": self.gold["amend"]["after_count"],
                }
            )
            record = load(case / "record.json")
            prov = record["provenance"]
            require(
                record["origin"] == "inbox_plan"
                and prov["plan_id"] == pid
                and prov["work_id"] == decision["work_id"],
                "wrong dispatch",
            )
            require(
                set(record["inputs"]) == set(expected) == set(prov["inputs"]),
                "unexpected destination keys",
            )
            fact_map = {f["key"]: f for f in decision["facts"]}
            for key, value in expected.items():
                require(same_value(key, record["inputs"][key], value), f"wrong worker input {key}")
                link = prov["inputs"][key]
                fact = fact_map[link["fact_key"]]
                require(
                    link["receipt_id"] == fact["receipt_id"]
                    and fact["value"] == record["inputs"][key],
                    f"wrong source {key}",
                )
                if role == "new_subject" or key == "packages.count":
                    require(link["receipt_id"] == receipts[role]["id"], f"foreign source {key}")
            result = load(case / "result.json")
            require(
                outcome["worker_exit"] == 0 and result["status"] == "success",
                f"plan {pid}: validated inputs, worker {result['status']}: {result.get('reason')}",
            )
        return {"completed_workers": 2}

    def effects(self):
        return check_effects(
            table(self.old_db, "shipments"),
            table(self.db, "shipments"),
            table(self.old_db, "audit_events"),
            table(self.db, "audit_events"),
            self.gold,
        )

    def screens(self):
        pictures = {}
        for role, action in (
            ("new_subject", "shipment_created"),
            ("correction_body", "shipment_updated"),
        ):
            expected = self.gold["create"] if role == "new_subject" else self.gold["amend"]
            hbl = expected["hbl_no"]
            qty = (
                expected["packages"]["count"] if role == "new_subject" else expected["after_count"]
            )
            case, _ = self.worker(self.plan(role)[0])
            row = find_row(table(case.parent / "after.db", "shipments"), hbl)
            before = table(case.parent / "before.db", "audit_events")
            new = [
                e
                for k, e in table(case.parent / "after.db", "audit_events").items()
                if k not in before
            ]
            save = one(
                (
                    e
                    for e in new
                    if e["event_type"] == action and e["shipment_id"] == row["id"] and e["allowed"]
                ),
                hbl,
            )
            post = [
                e
                for e in new
                if e["event_type"] == "shipment_lookup"
                and e["details"] == hbl
                and e["allowed"]
                and e["id"] > save["id"]
            ]
            require(post, f"no post-save search for {hbl}")
            pictures[hbl] = requery(
                case, hbl, qty, row["id"], min(datetime.fromisoformat(e["timestamp"]) for e in post)
            )
        return pictures

    def repeat(self):
        result = load(self.run / "repeat.json")
        p = result["plan_next"]
        require(
            result["new_receipt_ids"] == []
            and p["status"] == "empty"
            and p["plan_id"] is None
            and p["model_calls"] == 0,
            "replay caused work",
        )
        for old, end, names in (
            ("erp-before-repeat.db", self.db, [("shipments", "id"), ("audit_events", "id")]),
            (
                "inbox-before-repeat.db",
                self.ib,
                [("receipts", "id"), ("plans", "id"), ("inbox_references", "receipt_id")],
            ),
        ):
            for name, key in names:
                require(
                    table(self.run / old, name, key) == table(end, name, key),
                    f"repeat changed {name}",
                )
        return {"new_receipts": 0, "new_plans": 0, "model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--daily-db", type=Path, required=True)
    args = parser.parse_args()
    audit = Audit(args.run.resolve(), args.daily_db, load(FIXTURES / "grading/expected.json"))
    gates = {}
    for check in (
        audit.initial,
        audit.new,
        audit.amend,
        audit.reference,
        audit.dispatch,
        audit.effects,
        audit.screens,
        audit.repeat,
    ):
        try:
            info = {"status": "pass", "evidence": check()}
        except FileNotFoundError as exc:
            info = {"status": "unverified", "reason": str(exc)}
        except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            info = {"status": "fail", "reason": str(exc)}
        gates[check.__name__] = info
    passed = sum(item["status"] == "pass" for item in gates.values())
    print(
        json.dumps(
            {"passed": passed == len(gates), "score": passed, "of": len(gates), "gates": gates},
            ensure_ascii=False,
            indent=2,
        )
    )
    return int(passed != len(gates))


if __name__ == "__main__":
    raise SystemExit(main())
