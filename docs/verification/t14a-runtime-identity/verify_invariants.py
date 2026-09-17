"""Compare read-only T14A observations with the forensic starting state."""

import json
import sys
from pathlib import Path

directory = Path(__file__).resolve().parent
suffix = sys.argv[1]
incident_before = json.loads((directory / "incident-before.json").read_text())
incident_after = json.loads((directory / f"incident-{suffix}.json").read_text())
integrity_before = json.loads((directory / "integrity-before.json").read_text())
integrity_after = json.loads((directory / f"integrity-{suffix}.json").read_text())
incident = {key: incident_before[key] == incident_after[key] for key in (
    "upload", "pipeline", "configuration", "readiness", "jobs", "steps",
    "all_running", "market_context", "migration", "jobs_after_baseline",
)}
historical = {key: value == integrity_after["historical"][key]
              for key, value in integrity_before["historical"].items()}
result = {"incidentUnchanged": incident, "historicalUnchanged": historical,
          "ceilingsUnchanged": integrity_before["ceilings"] == integrity_after["ceilings"]}
assert all(incident.values()), incident
assert all(historical.values()), historical
assert result["ceilingsUnchanged"]
(directory / f"invariants-{suffix}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(result)
