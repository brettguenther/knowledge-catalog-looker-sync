"""Looker Client wrapping looker-cli for Looker operations."""

from pathlib import Path
import shutil
import subprocess
from typing import List, Set, Tuple


class LookerClient:
    """Client for interacting with Looker using looker-cli."""

    def __init__(self, cli_path: str = "looker-cli"):
        self.cli_path_name = cli_path
        self._cli_path = shutil.which(cli_path)
        self._created_dirs: Set[str] = set()

    @property
    def cli_path(self) -> str:
        if not self._cli_path:
            raise FileNotFoundError(f"looker-cli executable '{self.cli_path_name}' not found in PATH.")
        return self._cli_path

    def ensure_dev_mode(self) -> None:
        """Ensures the current Looker session is in dev mode."""
        cmd = [self.cli_path, "session", "update", "dev"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to switch Looker session to dev mode: {res.stderr or res.stdout}")

    def checkout_branch(self, project_id: str, branch: str = "master") -> None:
        """Checks out the target branch in the project dev workspace."""
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
        """Lists all files in the Looker project."""
        cmd = [self.cli_path, "project", "file", "ls", project_id]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to list files in project '{project_id}': {res.stderr or res.stdout}")

        files = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("|") and not line.startswith("+") and not "PATH" in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 2:
                    files.append(parts[1])
        return files

    def create_or_update_file(self, project_id: str, file_path: str, content: str) -> None:
        """Creates or updates a file in the Looker project using looker-cli."""
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
        """Validates the LookML in the Looker project."""
        cmd = [self.cli_path, "project", "validate", project_id]
        res = subprocess.run(cmd, capture_output=True, text=True)
        output = (res.stdout + "\n" + res.stderr).strip()
        is_valid = res.returncode == 0 and ("Project is valid" in output or not output)
        return is_valid, output

    def deploy_to_production(self, project_id: str) -> str:
        """Deploys the active branch of the project to production."""
        cmd = [self.cli_path, "project", "deploy", project_id]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to deploy project '{project_id}' to production: {res.stderr or res.stdout}")
        return res.stdout.strip()
