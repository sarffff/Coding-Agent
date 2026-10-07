from __future__ import annotations

import re
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from .config import Settings
from .git_service import PROTECTED_BRANCHES
from .process_env import child_env, is_process_startup_failure
from .repository_service import RepositoryError


REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")


def protected_branch_error(branch: str) -> RepositoryError:
    return RepositoryError("PROTECTED_BRANCH_PUSH_PROHIBITED", f"Pushing to protected branch '{branch}' is forbidden.")


@dataclass(slots=True)
class RemoteRepoInfo:
    host: str
    owner: str
    repo: str
    default_branch: str = "main"


@dataclass(slots=True)
class PullRequestData:
    number: int
    title: str
    body: str
    html_url: str
    state: str
    source_branch: str
    target_branch: str
    draft: bool


class RemoteProvider(ABC):
    """Abstract interface for remote Git hosting providers."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def parse_remote_url(self, remote_url: str) -> tuple[str, str, str]:
        """Parse (host, owner, repo) from an HTTP(S) or SSH Git URL."""
        url = remote_url.strip()
        # SSH format: git@github.com:owner/repo.git
        ssh_match = re.fullmatch(r"^git@([^:]+):([^/]+)/(.+?)(?:\.git)?$", url)
        if ssh_match:
            host, owner, repo = ssh_match.groups()
            return host.lower(), owner, repo

        # HTTP/HTTPS format: https://github.com/owner/repo.git
        if "://" in url:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            parts = [p for p in parsed.path.strip("/").split("/") if p]
            if len(parts) >= 2:
                owner = parts[0]
                repo = parts[1].removesuffix(".git")
                return host, owner, repo

        raise RepositoryError("INVALID_REMOTE_URL", f"Cannot parse remote URL: {remote_url}")

    def validate_remote_url(self, remote_url: str) -> tuple[str, str, str]:
        """Validate remote URL against allowed hosts and repositories."""
        host, owner, repo = self.parse_remote_url(remote_url)
        allowed_hosts = [h.lower() for h in self.settings.allowed_remote_hosts]
        if host not in allowed_hosts:
            raise RepositoryError(
                "DISALLOWED_REMOTE_HOST",
                f"Host '{host}' is not in the allowed remote hosts list: {allowed_hosts}",
            )

        if self.settings.allowed_repositories:
            slug = f"{owner}/{repo}".lower()
            allowed = [r.lower() for r in self.settings.allowed_repositories]
            if slug not in allowed:
                raise RepositoryError(
                    "DISALLOWED_REPOSITORY",
                    f"Repository '{slug}' is not in the allowed repository list: {allowed}",
                )

        return host, owner, repo

    def repo_slug(self, remote_url: str) -> str:
        """Normalized `host/owner/repo` identity used to key remote state."""
        host, owner, repo = self.validate_remote_url(remote_url)
        return f"{host}/{owner}/{repo}".lower()

    @abstractmethod
    async def validate_repository(self, remote_url: str) -> RemoteRepoInfo:
        """Verify remote repository exists and is accessible."""
        ...

    @abstractmethod
    async def check_branch_exists(self, remote_url: str, branch: str) -> bool:
        """Check if branch exists on the remote."""
        ...

    @abstractmethod
    async def push_task_branch(self, repo_path: Path, remote_url: str, branch: str, remote: str = "origin") -> str:
        """Publish one reviewed task branch to the remote without force-pushing."""
        ...

    @abstractmethod
    async def create_pull_request(
        self,
        remote_url: str,
        source_branch: str,
        target_branch: str,
        title: str,
        body: str,
        draft: bool = True,
    ) -> PullRequestData:
        """Create a draft or open pull request."""
        ...

    @abstractmethod
    async def get_pull_request(self, remote_url: str, number: int) -> PullRequestData | None:
        """Retrieve pull request metadata by number."""
        ...


class GitHubRemoteProvider(RemoteProvider):
    """GitHub REST API implementation."""

    def _api_base(self, host: str) -> str:
        if host == "github.com":
            return "https://api.github.com"
        return f"https://{host}/api/v3"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        return headers

    async def validate_repository(self, remote_url: str) -> RemoteRepoInfo:
        host, owner, repo = self.validate_remote_url(remote_url)
        url = f"{self._api_base(host)}/repos/{owner}/{repo}"
        async with httpx.AsyncClient(timeout=self.settings.remote_timeout_seconds) as client:
            try:
                resp = await client.get(url, headers=self._headers())
            except httpx.RequestError as exc:
                raise RepositoryError("REMOTE_NETWORK_ERROR", f"Failed to reach GitHub: {exc}") from exc

            if resp.status_code == 401:
                raise RepositoryError("AUTHENTICATION_FAILED", "GitHub token is invalid or missing.")
            if resp.status_code == 403:
                raise RepositoryError("RATE_LIMITED_OR_FORBIDDEN", "GitHub rate limit exceeded or access forbidden.")
            if resp.status_code == 404:
                raise RepositoryError("REMOTE_NOT_FOUND", f"Repository {owner}/{repo} not found on {host}.")
            if resp.status_code >= 400:
                raise RepositoryError("REMOTE_ERROR", f"GitHub API error: {resp.status_code} - {resp.text}")

            data = resp.json()
            return RemoteRepoInfo(host=host, owner=owner, repo=repo, default_branch=data.get("default_branch", "main"))

    async def check_branch_exists(self, remote_url: str, branch: str) -> bool:
        host, owner, repo = self.validate_remote_url(remote_url)
        url = f"{self._api_base(host)}/repos/{owner}/{repo}/branches/{quote(branch, safe='')}"
        async with httpx.AsyncClient(timeout=self.settings.remote_timeout_seconds) as client:
            try:
                resp = await client.get(url, headers=self._headers())
            except httpx.RequestError as exc:
                raise RepositoryError("REMOTE_NETWORK_ERROR", f"Failed to reach {host}: {exc}") from exc
            return resp.status_code == 200

    async def push_task_branch(self, repo_path: Path, remote_url: str, branch: str, remote: str = "origin") -> str:
        if branch in PROTECTED_BRANCHES:
            raise protected_branch_error(branch)
        if not REMOTE_NAME_RE.fullmatch(remote):
            raise RepositoryError("INVALID_REMOTE_NAME", "Choose a valid Git remote name.")
        host = self.validate_remote_url(remote_url)[0]

        # `git push` authenticates through the operator's own credential helper;
        # FORGE_GITHUB_TOKEN only authorizes the REST calls, never the URL.
        env = child_env({"GIT_TERMINAL_PROMPT": "0"})
        command = ["git", "push", remote, f"refs/heads/{branch}:refs/heads/{branch}"]
        try:
            result = subprocess.run(
                command,
                cwd=str(repo_path),
                env=env,
                capture_output=True,
                text=True,
                timeout=self.settings.remote_timeout_seconds * 2,
            )
        except subprocess.TimeoutExpired as exc:
            raise RepositoryError("REMOTE_TIMEOUT", f"Git push to {host} did not finish in time.") from exc

        if is_process_startup_failure(result.returncode):
            raise RepositoryError("GIT_UNAVAILABLE", "Git could not start in this environment.")
        if result.returncode != 0:
            raise RepositoryError("PUSH_REJECTED", f"Git push to {host} was rejected: {self._mask(result.stderr)}")

        head = subprocess.run(
            ["git", "rev-parse", "--verify", f"{branch}^{{commit}}"],
            cwd=str(repo_path),
            env=env,
            capture_output=True,
            text=True,
        )
        if head.returncode != 0:
            raise RepositoryError("PUSHED_COMMIT_UNRESOLVED", "The pushed branch commit could not be read back.")
        return head.stdout.strip()

    def _mask(self, text: str) -> str:
        masked = (text or "").strip()
        if self.settings.github_token:
            masked = masked.replace(self.settings.github_token, "***")
        return masked[:500]

    async def create_pull_request(
        self,
        remote_url: str,
        source_branch: str,
        target_branch: str,
        title: str,
        body: str,
        draft: bool = True,
    ) -> PullRequestData:
        host, owner, repo = self.validate_remote_url(remote_url)
        url = f"{self._api_base(host)}/repos/{owner}/{repo}/pulls"
        payload = {
            "title": title,
            "body": body,
            "head": source_branch,
            "base": target_branch,
            "draft": draft,
        }
        async with httpx.AsyncClient(timeout=self.settings.remote_timeout_seconds) as client:
            try:
                resp = await client.post(url, headers=self._headers(), json=payload)
            except httpx.RequestError as exc:
                raise RepositoryError("REMOTE_NETWORK_ERROR", f"Failed to reach GitHub: {exc}") from exc

            if resp.status_code == 422:
                # PR may already exist for this branch; query and reconcile
                reconciled = await self._find_existing_pr(client, host, owner, repo, source_branch)
                if reconciled:
                    return reconciled

            if resp.status_code == 401:
                raise RepositoryError("AUTHENTICATION_FAILED", "GitHub token is invalid or missing.")
            if resp.status_code == 403:
                raise RepositoryError("RATE_LIMITED_OR_FORBIDDEN", "GitHub rate limit exceeded or access forbidden.")
            if resp.status_code >= 400:
                raise RepositoryError("PR_CREATION_FAILED", f"GitHub PR creation failed: {resp.status_code} - {resp.text}")

            data = resp.json()
            return PullRequestData(
                number=data["number"],
                title=data["title"],
                body=data.get("body", "") or "",
                html_url=data["html_url"],
                state=data["state"],
                source_branch=source_branch,
                target_branch=target_branch,
                draft=data.get("draft", False),
            )

    async def _find_existing_pr(
        self, client: httpx.AsyncClient, host: str, owner: str, repo: str, branch: str
    ) -> PullRequestData | None:
        url = f"{self._api_base(host)}/repos/{owner}/{repo}/pulls"
        params = {"head": f"{owner}:{branch}", "state": "all"}
        resp = await client.get(url, headers=self._headers(), params=params)
        if resp.status_code == 200:
            items = resp.json()
            if items:
                pr = items[0]
                return PullRequestData(
                    number=pr["number"],
                    title=pr["title"],
                    body=pr.get("body", "") or "",
                    html_url=pr["html_url"],
                    state=pr["state"],
                    source_branch=branch,
                    target_branch=pr["base"]["ref"],
                    draft=pr.get("draft", False),
                )
        return None

    async def get_pull_request(self, remote_url: str, number: int) -> PullRequestData | None:
        host, owner, repo = self.validate_remote_url(remote_url)
        url = f"{self._api_base(host)}/repos/{owner}/{repo}/pulls/{number}"
        async with httpx.AsyncClient(timeout=self.settings.remote_timeout_seconds) as client:
            try:
                resp = await client.get(url, headers=self._headers())
            except httpx.RequestError as exc:
                raise RepositoryError("REMOTE_NETWORK_ERROR", f"Failed to reach {host}: {exc}") from exc
            if resp.status_code == 404:
                return None
            if resp.status_code >= 400:
                raise RepositoryError("REMOTE_ERROR", f"GitHub API error: {resp.status_code} - {resp.text}")
            data = resp.json()
            return PullRequestData(
                number=data["number"],
                title=data["title"],
                body=data.get("body", "") or "",
                html_url=data["html_url"],
                state=data["state"],
                source_branch=data["head"]["ref"],
                target_branch=data["base"]["ref"],
                draft=data.get("draft", False),
            )


class FakeRemoteProvider(RemoteProvider):
    """In-memory hermetic RemoteProvider for unit tests and offline demos.

    Remote state is process-global so it survives an API restart, the way a real
    remote does. `reset()` clears it between tests or demo runs.
    """

    _repositories: dict[str, RemoteRepoInfo] = {}
    _branches: dict[str, dict[str, str]] = {}  # {repo_slug: {branch: sha}}
    _pull_requests: dict[str, list[PullRequestData]] = {}  # {repo_slug: [pr]}
    _pushes: list[tuple[str, str, str]] = []  # {(slug, branch, sha)} for side-effect assertions

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.repositories = FakeRemoteProvider._repositories
        self.branches = FakeRemoteProvider._branches
        self.pull_requests = FakeRemoteProvider._pull_requests
        self.pushes = FakeRemoteProvider._pushes

    @classmethod
    def reset(cls) -> None:
        cls._repositories.clear()
        cls._branches.clear()
        cls._pull_requests.clear()
        cls._pushes.clear()

    def register_repo(self, host: str, owner: str, repo: str, default_branch: str = "main") -> None:
        slug = f"{host}/{owner}/{repo}".lower()
        self.repositories[slug] = RemoteRepoInfo(host=host, owner=owner, repo=repo, default_branch=default_branch)
        self.branches.setdefault(slug, {})[default_branch] = "0" * 40

    async def validate_repository(self, remote_url: str) -> RemoteRepoInfo:
        slug = self.repo_slug(remote_url)
        if slug not in self.repositories:
            host, owner, repo = self.validate_remote_url(remote_url)
            self.register_repo(host, owner, repo)
        return self.repositories[slug]

    async def check_branch_exists(self, remote_url: str, branch: str) -> bool:
        return branch in self.branches.get(self.repo_slug(remote_url), {})

    async def push_task_branch(self, repo_path: Path, remote_url: str, branch: str, remote: str = "origin") -> str:
        if branch in PROTECTED_BRANCHES:
            raise protected_branch_error(branch)
        slug = self.repo_slug(remote_url)

        head = subprocess.run(
            ["git", "rev-parse", "--verify", f"{branch}^{{commit}}"],
            cwd=str(repo_path),
            env=child_env(),
            capture_output=True,
            text=True,
        )
        if head.returncode != 0:
            raise RepositoryError("PUSHED_COMMIT_UNRESOLVED", f"Branch '{branch}' does not exist in {repo_path}.")
        sha = head.stdout.strip()
        self.branches.setdefault(slug, {})[branch] = sha
        self.pushes.append((slug, branch, sha))
        return sha

    async def create_pull_request(
        self,
        remote_url: str,
        source_branch: str,
        target_branch: str,
        title: str,
        body: str,
        draft: bool = True,
    ) -> PullRequestData:
        host, owner, repo = self.validate_remote_url(remote_url)
        slug = f"{host}/{owner}/{repo}".lower()
        prs = self.pull_requests.setdefault(slug, [])

        # Check existing PR for same branch
        for existing in prs:
            if existing.source_branch == source_branch and existing.state != "closed":
                return existing

        number = len(prs) + 1
        pr = PullRequestData(
            number=number,
            title=title,
            body=body,
            html_url=f"https://{host}/{owner}/{repo}/pull/{number}",
            state="open",
            source_branch=source_branch,
            target_branch=target_branch,
            draft=draft,
        )
        prs.append(pr)
        return pr

    async def get_pull_request(self, remote_url: str, number: int) -> PullRequestData | None:
        host, owner, repo = self.validate_remote_url(remote_url)
        slug = f"{host}/{owner}/{repo}".lower()
        for pr in self.pull_requests.get(slug, []):
            if pr.number == number:
                return pr
        return None
