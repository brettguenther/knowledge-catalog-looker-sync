"""Git and GitHub Provider Client for automated branch and PR creation."""

import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Dict, Optional, Tuple
import urllib.request
import urllib.error


class GitHubProvider:
    """Manages Git branch creation, committing, pushing, and GitHub PR creation."""

    def __init__(self, repo_slug: str, token: Optional[str] = None, project_id: Optional[str] = None):
        """
        Args:
            repo_slug: Target repo in format 'owner/repo' (e.g. 'org/lookml-repo').
            token: GitHub Personal Access Token. If None, resolves from env or Secret Manager.
            project_id: GCP project ID for Secret Manager lookup if token is not provided.
        """
        self.repo_slug = repo_slug
        self.project_id = project_id
        self.token = token or self._resolve_github_token()

    def _resolve_github_token(self) -> Optional[str]:
        for key in ("GITHUB_TOKEN", "GITHUB_PAT"):
            if os.environ.get(key):
                return os.environ[key].strip()

        # Check local .env file
        env_file = Path(".env")
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                for key in ("GITHUB_TOKEN", "GITHUB_PAT"):
                    if line.startswith(f"{key}="):
                        val = line.split("=", 1)[1].strip("\"'")
                        if val:
                            return val

        # Try Google Secret Manager via Python client library (ADC)
        if self.project_id:
            try:
                from google.cloud import secretmanager
                sm_client = secretmanager.SecretManagerServiceClient()
                secret_name = f"projects/{self.project_id}/secrets/GITHUB_TOKEN/versions/latest"
                response = sm_client.access_secret_version(request={"name": secret_name})
                secret_val = response.payload.data.decode("UTF-8").strip()
                if secret_val:
                    return secret_val
            except Exception:
                pass

            # Fallback to gcloud CLI if installed locally
            try:
                cmd = [
                    "gcloud", "secrets", "versions", "access", "latest",
                    "--secret=GITHUB_TOKEN",
                    f"--project={self.project_id}",
                ]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception:
                pass

        return None

    def create_pull_request(
        self,
        lookml_files: Dict[str, str],
        base_branch: str = "master",
        branch_prefix: str = "kc-sync/update",
        title: Optional[str] = None,
        body: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Clones or stages changes, commits, pushes, and opens a GitHub Pull Request.
        
        Args:
            lookml_files: Dict mapping relative repo file paths to file content strings.
            base_branch: Base branch to target (e.g. 'master' or 'main').
            branch_prefix: Prefix for the generated feature branch.
            title: Title for the Pull Request.
            body: Markdown body for the Pull Request.
            
        Returns:
            Dict containing pr_created (bool), pr_url (str), branch (str), diff (str).
        """
        if not self.token:
            raise ValueError(
                "GitHub token not found. Please provide GITHUB_TOKEN environment variable "
                "or store it in Google Secret Manager under secret name 'GITHUB_TOKEN'."
            )

        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        branch_name = f"{branch_prefix}-{timestamp}"
        temp_dir = Path("/tmp") / f"git_sync_{timestamp}"
        
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
            auth_repo_url = f"https://x-access-token:{self.token}@github.com/{self.repo_slug}.git"

            # 1. Clone base branch
            clone_cmd = ["git", "clone", "--depth=1", "--branch", base_branch, auth_repo_url, str(temp_dir)]
            subprocess.run(clone_cmd, capture_output=True, text=True, check=True)

            # 2. Checkout new feature branch
            subprocess.run(["git", "checkout", "-b", branch_name], cwd=temp_dir, capture_output=True, text=True, check=True)

            # 3. Write generated LookML files
            changed = False
            for rel_path, content in lookml_files.items():
                target_file = temp_dir / rel_path
                target_file.parent.mkdir(parents=True, exist_ok=True)
                existing = target_file.read_text() if target_file.exists() else ""
                if existing != content:
                    target_file.write_text(content)
                    changed = True

            # 4. Stage specifically the managed LookML files
            for rel_path in lookml_files.keys():
                target_file = temp_dir / rel_path
                if target_file.exists():
                    subprocess.run(["git", "add", rel_path], cwd=temp_dir, check=True)

            diff_stat_res = subprocess.run(["git", "diff", "--cached", "--stat"], cwd=temp_dir, capture_output=True, text=True)
            diff_cached_res = subprocess.run(["git", "diff", "--cached"], cwd=temp_dir, capture_output=True, text=True)
            
            if not diff_stat_res.stdout.strip():
                return {
                    "pr_created": False,
                    "message": "No LookML changes detected. Repository is up-to-date.",
                    "diff": "",
                }

            # 5. Commit changes
            git_user = os.environ.get("GIT_AUTHOR_NAME", "Knowledge Catalog Sync Bot")
            git_email = os.environ.get("GIT_AUTHOR_EMAIL", "kc-sync-bot@google.com")
            subprocess.run(["git", "config", "user.name", git_user], cwd=temp_dir, check=True)
            subprocess.run(["git", "config", "user.email", git_email], cwd=temp_dir, check=True)
            commit_msg = f"feat(lookml): sync knowledge catalog metadata [skip ci]\n\nAutomated metadata sync: {timestamp}"
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=temp_dir, capture_output=True, text=True, check=True)

            # 6. Push to remote
            push_cmd = ["git", "push", "-u", auth_repo_url, branch_name]
            subprocess.run(push_cmd, cwd=temp_dir, capture_output=True, text=True, check=True)

            # 7. Open Pull Request via GitHub API
            pr_title = title or f"KC Metadata Sync - {timestamp}"
            pr_body = body or (
                "### Knowledge Catalog Metadata Synchronization\n\n"
                "Automated PR generated from Google Cloud Knowledge Catalog (Dataplex).\n\n"
                "Updates certified attributes, business labels, and semantic metadata across base views."
            )

            api_url = f"https://api.github.com/repos/{self.repo_slug}/pulls"
            payload = json.dumps({
                "title": pr_title,
                "head": branch_name,
                "base": base_branch,
                "body": pr_body,
            }).encode("utf-8")

            req = urllib.request.Request(
                api_url,
                data=payload,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                    "User-Agent": "looker-kc-sync-bot",
                },
                method="POST",
            )

            with urllib.request.urlopen(req) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))

            pr_url = resp_data.get("html_url", "")
            return {
                "pr_created": True,
                "pr_url": pr_url,
                "branch": branch_name,
                "diff": diff_cached_res.stdout,
                "message": f"Pull Request successfully opened: {pr_url}",
            }

        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
