"""Create or inspect the local API state store."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.config import get_settings  # noqa: E402
from app.store import SCHEMA_VERSION, StateStore  # noqa: E402

SCOPES = ("repositories", "tasks", "approvals", "audit_events", "checkpoints")


def main() -> None:
    settings = get_settings()
    store = StateStore(settings.state_dir / "state.db")
    print(f"state directory: {(settings.state_dir / 'state.db').resolve()}")
    print(f"schema version: {SCHEMA_VERSION}")
    for scope in SCOPES:
        print(f"  {scope}: {len(store.items(scope))}")
    store.close()


if __name__ == "__main__":
    main()
