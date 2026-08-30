"""Serverless entrypoint for the hosted dashboard.

The deployed site is read-only by construction: it serves run records and
ledgers that were produced locally and committed alongside the code. Nothing
here calls a model, so the deployment needs no API key and cannot spend money.

`src/` goes on the path explicitly because the host installs requirements but
does not install this project as a package.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from controlplane.dashboard.app import app  # noqa: E402

__all__ = ["app"]
