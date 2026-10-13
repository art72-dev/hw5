"""The real API on port 8000, but with the scripted test model instead of gpt-6-luna.

For checking the dashboard end to end without spending tokens. The audit goes
to a temp file, so output/audit_trail.json isn't touched. Each scripted model
step waits a moment so the live feed visibly streams.

From hw5/:  .venv/bin/python backend/tests/demo_backend.py
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

HW = Path(__file__).resolve().parents[2]  # hw5/ (this file is in backend/tests/)
WORK_DIR = Path(tempfile.mkdtemp(prefix="campus_customs_"))  # never the real working copy
os.environ["CAMPUS_CUSTOMS_WORK_DIR"] = str(WORK_DIR)
sys.path.insert(0, str(HW / "backend"))
sys.path.insert(0, str(HW / "backend" / "tests"))
os.chdir(HW / "backend")

import uvicorn  # noqa: E402
from pydantic_ai.models.function import FunctionModel  # noqa: E402

import main  # noqa: E402
from audit import AuditTrail  # noqa: E402
from test_team_offline import scripted  # noqa: E402


async def slow_scripted(messages, info):
    await asyncio.sleep(0.5)
    return scripted(messages, info)


main.app.state.model = FunctionModel(slow_scripted)
main.app.state.audit = AuditTrail(Path(tempfile.mkdtemp()) / "audit_trail.json")

if __name__ == "__main__":
    uvicorn.run(main.app, host="127.0.0.1", port=8000)
