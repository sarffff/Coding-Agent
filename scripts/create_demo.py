"""Create a repeatable local demo without overwriting an existing checkout."""
import argparse
import re
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="phase-1", help="A unique local demo name")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,48}", args.name):
        parser.error("Use a short name containing lowercase letters, numbers and hyphens.")
    root = Path(__file__).resolve().parents[1]
    demo_root = (root / ".forge" / "demo").resolve()
    destination = (demo_root / args.name).resolve()
    if not destination.is_relative_to(demo_root):
        parser.error("The destination must stay inside the local demo directory.")
    if destination.exists():
        if (destination / ".git").is_dir():
            print(f"Existing demo preserved: {destination}")
            return
        parser.error("This destination already exists. Choose another --name.")
    destination.mkdir(parents=True)
    for name in ("calculator.py", "test_calculator.py", "README.md"):
        shutil.copyfile(root / "examples" / "phase-1-demo" / name, destination / name)
    (destination / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    def git(*arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=destination, check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("config", "user.name", "Forge Demo")
    git("config", "user.email", "forge-demo@example.invalid")
    git("add", ".")
    git("commit", "-qm", "Initial Phase 1 demo")
    print(f"Demo repository: {destination}")
    print("Register this path in Desktop. Create a task branch before the first coding iteration.")


if __name__ == "__main__":
    main()
