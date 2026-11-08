"""Run a real model + MatterVis preview against the configured local app."""
from pathlib import Path

from contest.app import app

sample = Path(__file__).resolve().parents[1] / "contest" / "static" / "example.cif"
client = app.test_client()
with sample.open("rb") as handle:
    response = client.post("/api/jobs", data={
        "file": (handle, "example-urea.cif"),
        "prompt": "Make a clear ball-and-stick figure with hydrogens visible.",
    })
if response.status_code != 201:
    raise SystemExit(f"Preview failed ({response.status_code}): {response.get_json()}")
job = response.get_json()
preview = client.get(f"/api/jobs/{job['id']}/preview")
if preview.status_code != 200 or not preview.data.startswith(b"\x89PNG"):
    raise SystemExit("The preview was not a valid PNG.")
print(f"Real AI + MatterVis preview succeeded: style={job['plan']['style']}, png_bytes={len(preview.data)}, job={job['id']}")
