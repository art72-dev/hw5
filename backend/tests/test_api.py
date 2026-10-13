"""Offline test of every backend route (FastAPI TestClient), with the scripted model instead of gpt-6-luna.

Real routes, real MCP server, real working database; the audit goes to a
temp file. Flow: reset -> tickets -> run 101 -> events -> board -> approve
#501 (cash 3400 -> 2560) -> run 102 -> approve rent (-> 160) -> run 103 ->
approve price (no cash change) -> purchase refused (cash) -> reset (-> 3400).

From hw5/:  .venv/bin/python backend/tests/test_api.py
"""

import os
import sys
import tempfile
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
HW = Path(__file__).resolve().parents[2]  # hw5/ (this file is in backend/tests/)
WORK_DIR = Path(tempfile.mkdtemp(prefix="campus_customs_"))  # never the real working copy
os.environ["CAMPUS_CUSTOMS_WORK_DIR"] = str(WORK_DIR)
sys.path.insert(0, str(HW / "backend"))
sys.path.insert(0, str(HW / "backend" / "tests"))
os.chdir(HW / "backend")

from fastapi.testclient import TestClient  # noqa: E402
from pydantic_ai.models.function import FunctionModel  # noqa: E402

import main  # noqa: E402
from audit import AuditTrail  # noqa: E402
from test_team_offline import scripted  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, ok))
    print(("PASS  " if ok else "FAIL  ") + name)


def wait_for(c: TestClient, run_id: str, timeout: float = 120) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        run = c.get(f"/api/runs/{run_id}").json()
        if run["status"] != "running":
            return run
        time.sleep(0.5)
    raise TimeoutError(run_id)


def board_item(c: TestClient, ticket_id: int, kind: str) -> dict:
    return next(i for i in c.get("/api/board", params={"ticket_id": ticket_id, "kind": kind}).json()["items"])


def main_test(tmp: Path) -> None:
    main.app.state.model = FunctionModel(scripted)
    main.app.state.audit = AuditTrail(tmp / "audit_trail.json")  # set before startup: the API rebuilds runs from it
    with TestClient(main.app) as c:

        r = c.post("/api/reset")
        check("POST /api/reset -> working copy identical to original, checking $3,400",
              r.status_code == 200 and r.json()["refreshed"]["identical_to_original"] and r.json()["cash"]["balance"] == 3400.0)
        t = c.get("/api/tickets").json()
        check("GET /api/tickets -> 3 tickets, all open", t["count"] == 3 and [x["state"] for x in t["tickets"]] == ["open"] * 3)
        check("GET /api/cash -> checking $3,400 from cash_accounts", c.get("/api/cash").json()["balance"] == 3400.0)
        check("POST /api/tickets/999/run -> 404", c.post("/api/tickets/999/run").status_code == 404)

        r = c.post("/api/tickets/101/run")
        run101 = r.json()
        busy = c.post("/api/tickets/102/run")
        check("POST /api/tickets/101/run -> 202 running; a second run while busy -> 409",
              r.status_code == 202 and run101["status"] == "running" and busy.status_code == 409)
        check("reset is refused while a run is going -> 409", c.post("/api/reset").status_code == 409)
        done = wait_for(c, run101["run_id"])
        decision = done["result"]["outcomes"][0]["decision"] if done["result"] else None
        check("GET /api/runs/{id} -> done, ticket resolved when the run finishes",
              done["status"] == "done" and decision and decision["status"] == "resolved"
              and next(x for x in c.get("/api/tickets").json()["tickets"] if x["id"] == 101)["state"] == "resolved")
        check("running a resolved ticket again -> 409 (reset first)", c.post("/api/tickets/101/run").status_code == 409)
        check("single-ticket run skipped triage and did not refresh again", done["result"]["plan"] is None and done["refresh"] is False)

        ev = c.get("/api/events", params={"ticket_id": 101, "limit": 200}).json()
        agents = {e["agent"] for e in ev["events"]}
        tools = {t for e in ev["events"] for t in e["tools_used"]}
        check("GET /api/events -> Boss + the 3 specialists it chose, what they said and the tools they used",
              {"Boss", "Inventory", "Accounting", "Customer Service"} <= agents and "Facilities" not in agents
              and {"check_stock", "request_payment", "draft_customer_message", "update_ticket"} <= tools
              and any(e["said"] for e in ev["events"]))
        check("events come newest first", ev["events"][0]["timestamp"] >= ev["events"][-1]["timestamp"])

        req = board_item(c, 101, "payment_request")
        check("GET /api/board -> #501 payment request $840 pending (agent prepped, not paid)",
              req["amount"] == 840.0 and req["status"] == "pending_approval")
        check("GET /api/cash still $3,400 after the agents ran (agents can't move cash)", c.get("/api/cash").json()["balance"] == 3400.0)
        check("approve with an agent's name -> 400", c.post(f"/api/board/{req['id']}/approve", json={"approved_by": "Accounting"}).status_code == 400)
        a = c.post(f"/api/board/{req['id']}/approve", json={"approved_by": "Anjali (owner)"})
        body = a.json()
        check("POST /api/board/{id}/approve -> paid: payments row, invoice paid, cash 3400 -> 2560",
              a.status_code == 200 and body["payment"]["status"] == "paid" and body["cash_before"] == 3400.0
              and body["cash"]["balance"] == 2560.0 and body["payment"]["updated"]["invoices"]["status"] == "paid")
        check("approving the same item again -> 409 (no double payment)",
              c.post(f"/api/board/{req['id']}/approve", json={"approved_by": "Anjali (owner)"}).status_code == 409)

        run102 = wait_for(c, c.post("/api/tickets/102/run").json()["run_id"])
        rent = board_item(c, 102, "payment_request")
        a = c.post(f"/api/board/{rent['id']}/approve", json={"approved_by": "Anjali (owner)"}).json()
        check("ticket 102: run done, rent approved -> lease next_due 2026-10-02, cash 2560 -> 160",
              run102["status"] == "done" and a["payment"]["updated"]["leases"]["next_due"] == "2026-10-02" and a["cash"]["balance"] == 160.0)

        run103 = wait_for(c, c.post("/api/tickets/103/run").json()["run_id"])
        override = board_item(c, 103, "price_override")
        a = c.post(f"/api/board/{override['id']}/approve", json={"approved_by": "Anjali (owner)"}).json()
        check("ticket 103: price override approved -> no payment, cash stays $160 (a sale adds nothing)",
              run103["status"] == "done" and "payment" not in a and a["cash"]["balance"] == 160.0)
        purchase = board_item(c, 103, "vendor_draft")
        p = c.post(f"/api/board/{purchase['id']}/approve", json={"approved_by": "Anjali (owner)"})
        check("purchase of 12 hoodies (est. $264) refused: only $160 left (rule 6) -> 409",
              p.status_code == 409 and "can't cover" in p.json()["detail"])

        t = c.get("/api/tickets").json()
        check("GET /api/tickets -> all 3 resolved after their runs, last_run attached",
              [x["state"] for x in t["tickets"]] == ["resolved"] * 3 and all(x["last_run"] for x in t["tickets"]))
        human = c.get("/api/events", params={"agent": "human"}).json()["events"]
        check("human approvals and resets are in the audit trail", {e["event"] for e in human} >= {"human_decision", "reset"})

        r = c.post("/api/reset").json()
        check("POST /api/reset -> back to original: cash $3,400, board empty, tickets open",
              r["cash"]["balance"] == 3400.0 and c.get("/api/board").json()["count"] == 0
              and {x["status"] for x in c.get("/api/tickets").json()["tickets"]} == {"open"})

    passed = sum(ok for _, ok in results)
    print(f"\n{passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        main_test(Path(d))
