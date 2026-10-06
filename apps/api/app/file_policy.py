from pathlib import Path


IGNORED_DIRECTORIES = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "dist", "build", ".next",
    ".turbo", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".forge",
}
SENSITIVE_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials", "credentials.json"}


def is_protected_path(path: str | Path) -> bool:
    parts = Path(path).parts
    if any(part.lower() in IGNORED_DIRECTORIES for part in parts):
        return True
    name = Path(path).name.lower()
    if name == ".env" or (name.startswith(".env.") and not name.endswith((".example", ".sample"))):
        return True
    return name in SENSITIVE_NAMES or Path(name).suffix in {".pem", ".key", ".p12", ".pfx"}
