import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_integration import offline  # noqa: E402,F401  (shared fixture)
