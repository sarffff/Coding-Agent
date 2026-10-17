"""Isolated, deterministic API for browser acceptance; never uses real providers."""
import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="interaction")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not args.name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in args.name):
        parser.error("Use a lowercase name containing only letters, digits and hyphens.")
    directory = ROOT / ".forge" / "acceptance" / args.name
    workspace = directory / "workspace"
    for name in ("standard", "conservative", "repair", "stop", "recovery"):
        folder = workspace / name
        folder.mkdir(parents=True, exist_ok=True)
        for filename, content in {
            "app.py": "def value():\n    return 1\n",
            "test_app.py": "from app import value\n\ndef test_value():\n    assert value() == 2\n",
        }.items():
            path = folder / filename
            if not path.exists():
                path.write_text(content, encoding="utf-8")
    # Set before importing main: its service graph and CORS use these settings.
    os.environ.update(
        FORGE_WORKSPACE_ROOT=str(workspace),
        FORGE_STATE_DIR=str(directory / "state"),
        FORGE_LLM_PROVIDER="fake",
        FORGE_REMOTE_PROVIDER="fake",
    )
    from app import main as api
    from app.model_provider import FakeModelProvider, PatchProposal
    from app.models import PatchFile
    import uvicorn

    class AcceptanceModel(FakeModelProvider):
        async def propose_patch(self, goal, context_files, failure):
            if "slow" in goal.lower():
                await asyncio.sleep(30)
            value = 3 if "repair" in goal.lower() and not failure else 2
            return PatchProposal(
                files=[PatchFile(path="app.py", content=f"def value():\n    return {value}\n")],
                rationale="Deterministic interaction fixture; not a model quality result.",
            )

    api.agent_service.models = AcceptanceModel(api.settings)
    print(f"Acceptance workspace: {workspace}", flush=True)
    print("Use 'repair' in the goal for a failed first round; 'slow' to test stop.", flush=True)
    print("Existing files and task state are preserved for restart acceptance.", flush=True)
    uvicorn.run(api.app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
