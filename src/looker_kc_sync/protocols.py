"""Protocol abstractions for Catalog Sources and LookML Sinks/Deployers."""

from typing import Dict, List, Optional, Protocol, Tuple, runtime_checkable
from looker_kc_sync.models.catalog import CatalogEntry


@runtime_checkable
class CatalogSource(Protocol):
    """Protocol for fetching metadata assets from a data catalog."""

    def get_dataset_entries(
        self,
        dataset_id: str,
        table_names: Optional[List[str]] = None,
    ) -> List[CatalogEntry]:
        """Fetches catalog entries and aspects for tables in a dataset."""
        ...


@runtime_checkable
class LookMLDeployer(Protocol):
    """Protocol for deploying generated LookML files to Looker."""

    def ensure_dev_mode(self) -> None:
        """Ensures dev workspace is active."""
        ...

    def checkout_branch(self, project_id: str, branch: str = "master") -> None:
        """Checks out target working branch."""
        ...

    def create_or_update_file(self, project_id: str, file_path: str, content: str) -> None:
        """Creates or updates a single file in the project."""
        ...

    def validate_project(self, project_id: str) -> Tuple[bool, str]:
        """Validates project LookML."""
        ...
