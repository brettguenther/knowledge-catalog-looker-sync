"""Sync Orchestrator coordinating extraction, mapping, generation, deployment, and GitOps PRs."""

import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel

from looker_kc_sync.clients.dataplex import DataplexCatalogClient
from looker_kc_sync.clients.looker import LookerClient
from looker_kc_sync.generator.engine import LookMLGenerator
from looker_kc_sync.mapping.engine import SemanticMapper
from looker_kc_sync.mapping.profile import MappingProfile
from looker_kc_sync.models.lookml import LookMLView


def _expand_env(value: Any) -> Any:
    """Recursively expands ${VAR} and ${VAR:-default} patterns in strings, dicts, and lists."""
    if isinstance(value, str):
        pattern = re.compile(r"\$\{([^}^{]+)\}")

        def _repl(match):
            expr = match.group(1)
            if ":-" in expr:
                var, default = expr.split(":-", 1)
                return os.environ.get(var, default)
            return os.environ.get(expr, "")

        return pattern.sub(_repl, value)
    elif isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _load_dotenv(path: str = ".env") -> None:
    """Loads key-value pairs from a local .env file into os.environ if not already set."""
    env_file = Path(path)
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("\"'")
            if k and k not in os.environ:
                os.environ[k] = v


class SyncConfig(BaseModel):
    project_id: str
    location: str = "us-central1"
    dataset_id: str
    looker_project_id: str
    git_repo: Optional[str] = None
    git_base_branch: str = "master"
    connection_name: str
    model_name: str
    active_profile: str
    output_dir: str = "output"
    tables: Optional[List[str]] = None


class SyncOrchestrator:
    """End-to-end sync engine coordinating KC extraction and Looker LookML deployment."""

    def __init__(self, config_path: str):
        _load_dotenv()
        self.config_path = Path(config_path)
        if not self.config_path.exists():
            example_candidate = Path(str(self.config_path) + ".example")
            if example_candidate.exists():
                self.config_path = example_candidate

        with open(self.config_path, "r") as f:
            raw_cfg = yaml.safe_load(f) or {}
        raw_cfg = _expand_env(raw_cfg)

        # Allow environment variable overrides for remote deployment
        env_mappings = {
            "project_id": "PROJECT_ID",
            "location": "LOCATION",
            "dataset_id": "DATASET_ID",
            "looker_project_id": "LOOKER_PROJECT_ID",
            "git_repo": "GIT_REPO",
            "git_base_branch": "GIT_BASE_BRANCH",
            "connection_name": "CONNECTION_NAME",
            "model_name": "MODEL_NAME",
            "active_profile": "ACTIVE_PROFILE",
            "output_dir": "OUTPUT_DIR",
        }
        for field, env_key in env_mappings.items():
            if os.environ.get(env_key):
                raw_cfg[field] = os.environ[env_key]

        self.config = SyncConfig(**raw_cfg)

        # Load mapping profile
        profile_path = Path(self.config.active_profile)
        if not profile_path.is_absolute():
            profile_path = Path.cwd() / profile_path
        with open(profile_path, "r") as f:
            raw_profile = yaml.safe_load(f)
        self.profile = MappingProfile(**raw_profile)

        # Clients & Generator
        self.catalog_client = DataplexCatalogClient(
            project_id=self.config.project_id,
            location=self.config.location,
        )
        self.mapper = SemanticMapper(self.profile)
        template_dir = Path(__file__).parent / "generator" / "templates"
        self.generator = LookMLGenerator(template_dir)
        self._looker_client: Optional[LookerClient] = None

    @property
    def looker_client(self) -> LookerClient:
        if self._looker_client is None:
            self._looker_client = LookerClient()
        return self._looker_client

    def run(self, deploy: bool = True, create_pr: bool = False, scaffold: bool = False) -> Dict[str, Any]:
        """Executes full catalog extraction, LookML generation, Looker deployment, and optional PR creation."""
        results: Dict[str, Any] = {
            "tables_processed": 0,
            "views_generated": [],
            "files_written": [],
            "looker_deployed": False,
            "validation_valid": False,
            "validation_message": "",
            "pr_result": None,
            "errors": [],
        }

        # 1. Fetch entries from Knowledge Catalog
        entries = self.catalog_client.get_dataset_entries(
            dataset_id=self.config.dataset_id,
            table_names=self.config.tables,
        )
        if not entries:
            raise RuntimeError(f"No catalog entries found for dataset {self.config.dataset_id}")

        out_path = Path(self.config.output_dir)
        base_views_dir = out_path / "views" / "base"
        base_views_dir.mkdir(parents=True, exist_ok=True)
        if scaffold:
            curated_views_dir = out_path / "views" / "curated"
            models_dir = out_path / "models"
            curated_views_dir.mkdir(parents=True, exist_ok=True)
            models_dir.mkdir(parents=True, exist_ok=True)

        views: List[LookMLView] = []
        base_view_files: Dict[str, str] = {}

        # 2. Map & Generate Base Views (Machine-managed layer with error isolation)
        for entry in entries:
            try:
                view = self.mapper.map_entry_to_view(entry)
                views.append(view)
                results["tables_processed"] += 1
                results["views_generated"].append(view.view_name)

                # Render Base View (Machine-managed layer)
                base_lookml = self.generator.render_base_view(view)
                base_file_rel = f"views/base/{view.view_name}.base.view.lkml"
                base_file_local = out_path / base_file_rel
                base_file_local.write_text(base_lookml)
                results["files_written"].append(str(base_file_local))
                base_view_files[base_file_rel] = base_lookml
            except Exception as e:
                err_msg = f"Failed to map/generate view for table '{entry.entry_id}': {e}"
                print(f"Warning: {err_msg}")
                results["errors"].append(err_msg)

        if not base_view_files:
            raise RuntimeError(
                f"All table mappings failed for dataset '{self.config.dataset_id}'. Errors: {results['errors']}"
            )

        # 3. Optional local scaffolding (Starter curated refinements and model file)
        # Curated views (views/curated/) and models (models/) belong strictly to human analytics engineers.
        # They are ONLY generated locally when explicit scaffolding is requested (scaffold=True)
        # and MUST NEVER be deployed to remote Looker or submitted via automated PRs.
        if scaffold:
            for view in views:
                curated_file_rel = f"views/curated/{view.view_name}.view.lkml"
                curated_file_local = out_path / curated_file_rel
                if not curated_file_local.exists():
                    curated_lookml = self.generator.render_curated_refinement(view)
                    curated_file_local.write_text(curated_lookml)
                    results["files_written"].append(str(curated_file_local))

            model_file_rel = f"models/{self.config.model_name}.model.lkml"
            model_file_local = out_path / model_file_rel
            if not model_file_local.exists():
                model_lookml = self.generator.render_model(
                    model_name=self.config.model_name,
                    connection_name=self.config.connection_name,
                    views=views,
                )
                model_file_local.write_text(model_lookml)
                results["files_written"].append(str(model_file_local))

        # 4. Deploy to Looker via looker-cli
        # Automated deployment strictly updates machine-managed base views (views/base/*.base.view.lkml).
        # It MUST NOT deploy curated views or model files.
        if deploy:
            self.looker_client.ensure_dev_mode()
            self.looker_client.checkout_branch(self.config.looker_project_id, "master")

            for rel_path, content in base_view_files.items():
                self.looker_client.create_or_update_file(
                    project_id=self.config.looker_project_id,
                    file_path=rel_path,
                    content=content,
                )

            # Validate LookML in Looker
            is_valid, val_msg = self.looker_client.validate_project(self.config.looker_project_id)
            results["looker_deployed"] = True
            results["validation_valid"] = is_valid
            results["validation_message"] = val_msg

        # 5. Open GitOps Pull Request if requested
        if create_pr and self.config.git_repo:
            from looker_kc_sync.clients.git_provider import GitHubProvider
            git_client = GitHubProvider(
                repo_slug=self.config.git_repo,
                project_id=self.config.project_id,
            )
            # CRITICAL ARCHITECTURAL INVARIANT:
            # Automated GitOps PRs MUST only touch the machine-managed base views (views/base/*.base.view.lkml).
            # Curated views (views/curated/) and models (models/) belong to human analytics engineers
            # and must NEVER be modified or included in automated sync PRs.
            pr_res = git_client.create_pull_request(
                lookml_files=base_view_files,
                base_branch=self.config.git_base_branch,
            )
            results["pr_result"] = pr_res

        return results
