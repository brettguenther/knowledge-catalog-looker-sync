"""Looker Client wrapping official Looker SDK and looker-cli for Looker operations."""

import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, List, Optional, Set, Tuple

import looker_sdk
from looker_sdk import models40


class LookerClient:
    """Client for interacting with Looker using official Looker SDK, with looker-cli support."""

    def __init__(
        self,
        cli_path: str = "looker-cli",
        sdk: Optional[looker_sdk.methods40.Looker40SDK] = None,
    ):
        self.cli_path_name = cli_path
        self._cli_path = shutil.which(cli_path)
        self._created_dirs: Set[str] = set()
        self._sdk = sdk

    @property
    def sdk(self) -> Optional[looker_sdk.methods40.Looker40SDK]:
        """Lazily initializes the official Looker SDK client if credentials are configured."""
        if self._sdk is None:
            # Check if Looker SDK configuration is available in env or looker.ini
            has_env = bool(os.environ.get("LOOKERSDK_BASE_URL") and os.environ.get("LOOKERSDK_CLIENT_ID"))
            has_ini = Path("looker.ini").exists() or Path(os.path.expanduser("~/.looker/looker.ini")).exists()
            if has_env or has_ini:
                try:
                    self._sdk = looker_sdk.init40()
                except Exception as e:
                    print(f"Warning: Failed to initialize Looker SDK: {e}")
                    self._sdk = None
        return self._sdk

    @property
    def cli_path(self) -> str:
        if not self._cli_path:
            raise FileNotFoundError(f"looker-cli executable '{self.cli_path_name}' not found in PATH.")
        return self._cli_path

    def ensure_dev_mode(self) -> None:
        """Ensures the current Looker session is in dev mode."""
        if self.sdk:
            try:
                self.sdk.update_session(models40.WriteApiSession(workspace_id="dev"))
                return
            except Exception as e:
                print(f"Warning: SDK session update failed, falling back to CLI: {e}")

        cmd = [self.cli_path, "session", "update", "dev"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to switch Looker session to dev mode: {res.stderr or res.stdout}")

    def checkout_branch(self, project_id: str, branch: str = "master") -> None:
        """Checks out the target branch in the project dev workspace."""
        if self.sdk:
            try:
                self.sdk.update_git_branch(project_id, models40.WriteGitBranch(name=branch))
                return
            except Exception as e:
                print(f"Warning: SDK checkout branch failed, falling back to CLI: {e}")

        cmd = [self.cli_path, "project", "checkout", project_id, branch]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0 and "Already on" not in (res.stdout + res.stderr):
            raise RuntimeError(f"Failed to checkout branch '{branch}' in project '{project_id}': {res.stderr or res.stdout}")

    def ensure_directory(self, project_id: str, dir_path: str) -> None:
        """Ensures that a directory (and all its parents) exists in the project."""
        parts = Path(dir_path).parts
        accum = []
        for p in parts:
            accum.append(p)
            sub_dir = "/".join(accum)
            key = f"{project_id}:{sub_dir}"
            if key not in self._created_dirs:
                cmd = [self.cli_path, "project", "directory", "create", project_id, sub_dir]
                subprocess.run(cmd, capture_output=True, text=True)
                self._created_dirs.add(key)

    def list_files(self, project_id: str) -> List[str]:
        """Lists all files in the Looker project using Looker SDK or CLI."""
        if self.sdk:
            try:
                files = self.sdk.all_project_files(project_id)
                return [f.id for f in files if f.id]
            except Exception as e:
                print(f"Warning: SDK all_project_files failed, falling back to CLI: {e}")

        # CLI execution with --plain flag to avoid ASCII table parsing
        cmd = [self.cli_path, "project", "file", "ls", project_id, "--plain"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            # Fallback without --plain if not supported
            cmd = [self.cli_path, "project", "file", "ls", project_id]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"Failed to list files in project '{project_id}': {res.stderr or res.stdout}")

        files = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("+") or "PATH" in line:
                continue
            if line.startswith("|"):
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 2:
                    files.append(parts[1])
            else:
                files.append(line)
        return files

    def create_or_update_file(self, project_id: str, file_path: str, content: str) -> None:
        """Creates or updates a file in the Looker project."""
        self.ensure_dev_mode()

        # Ensure parent directories exist
        parent_dir = str(Path(file_path).parent)
        if parent_dir and parent_dir != ".":
            self.ensure_directory(project_id, parent_dir)

        # Attempt create with --force first
        cmd_create = [self.cli_path, "project", "file", "create", project_id, file_path, "-", "--force"]
        res_create = subprocess.run(cmd_create, input=content, capture_output=True, text=True)
        if res_create.returncode == 0:
            return

        # If create failed because file exists, try update
        cmd_update = [self.cli_path, "project", "file", "update", project_id, file_path, "-"]
        res_update = subprocess.run(cmd_update, input=content, capture_output=True, text=True)
        if res_update.returncode != 0:
            raise RuntimeError(
                f"Failed to write file '{file_path}' to project '{project_id}':\n"
                f"Create error: {res_create.stderr or res_create.stdout}\n"
                f"Update error: {res_update.stderr or res_update.stdout}"
            )

    def validate_project(self, project_id: str) -> Tuple[bool, str]:
        """Validates the LookML in the Looker project using Looker SDK or CLI."""
        if self.sdk:
            try:
                val_res = self.sdk.validate_project(project_id)
                errors = val_res.errors or []
                if not errors:
                    return True, "Project is valid."

                err_lines = []
                for err in errors:
                    loc = f"{err.file_path}:{err.line_number}" if err.file_path else "general"
                    err_lines.append(f"[{err.severity or 'ERROR'}] ({loc}) {err.message}")
                return False, "\n".join(err_lines)
            except Exception as e:
                print(f"Warning: SDK validate_project failed, falling back to CLI: {e}")

        cmd = [self.cli_path, "project", "validate", project_id]
        res = subprocess.run(cmd, capture_output=True, text=True)
        output = (res.stdout + "\n" + res.stderr).strip()
        is_valid = res.returncode == 0 and ("Project is valid" in output or not output)
        return is_valid, output

    def deploy_to_production(self, project_id: str) -> str:
        """Deploys the active branch of the project to production."""
        if self.sdk:
            try:
                res = self.sdk.deploy_to_production(project_id)
                return str(res)
            except Exception as e:
                print(f"Warning: SDK deploy_to_production failed, falling back to CLI: {e}")

        cmd = [self.cli_path, "project", "deploy", project_id]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to deploy project '{project_id}' to production: {res.stderr or res.stdout}")
        return res.stdout.strip()
