"""Install a pinned MatterVis checkout and its native web dependencies."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / ".local" / "vendor" / "MatterVis"
PATCH = ROOT / "vendor" / "mattervis-disorder-fixes.patch"
REVISION = "d1de653154d2dba2411c71781d00c22c36f45759"


def run(*args, check=True):
    return subprocess.run(args, check=check, cwd=ROOT)


if sys.prefix == sys.base_prefix:
    raise SystemExit("Create and activate a project virtual environment before running this script.")
VENDOR.parent.mkdir(parents=True, exist_ok=True)
if not VENDOR.exists():
    run("git", "clone", "--no-checkout", "https://github.com/SchrodingersCattt/MatterVis.git", str(VENDOR))
    run("git", "-C", str(VENDOR), "checkout", "--detach", REVISION)
head = subprocess.check_output(["git", "-C", str(VENDOR), "rev-parse", "HEAD"], text=True).strip()
if head != REVISION:
    raise SystemExit("The local MatterVis checkout is not the pinned revision; inspect it before proceeding.")
reverse_check = subprocess.run(
    ["git", "-C", str(VENDOR), "apply", "--unidiff-zero", "--reverse", "--check", str(PATCH)],
    cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
)
if reverse_check.returncode:
    run("git", "-C", str(VENDOR), "apply", "--unidiff-zero", "--check", str(PATCH))
    run("git", "-C", str(VENDOR), "apply", "--unidiff-zero", str(PATCH))
run(sys.executable, "-m", "pip", "install", "-e", str(VENDOR) + "[web]", "dash>=4.1,<5", "requests>=2.32,<3", "python-dotenv>=1,<2", "pytest>=8")
print("Pinned MatterVis web runtime is ready. Configure .env and run python -m contest.native_app.")
