"""LookML Code Generator & AST Validator using Jinja2 and lkml."""

import datetime
from pathlib import Path
from typing import Dict, List
import jinja2
import lkml
from looker_kc_sync.models.lookml import LookMLView


class LookMLGenerator:
    """Renders normalized LookML models into valid LookML files and validates with lkml."""

    def __init__(self, template_dir: Path):
        self.jinja_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(template_dir),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render_base_view(self, view: LookMLView) -> str:
        """Renders a base view file and validates it with lkml."""
        template = self.jinja_env.get_template("base_view.lkml.j2")
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        rendered = template.render(view=view, sync_timestamp=timestamp)

        # Validate with lkml AST parser
        try:
            lkml.load(rendered)
        except Exception as e:
            raise ValueError(f"Generated LookML for view {view.base_view_name} failed lkml validation: {e}")

        return rendered

    def render_curated_refinement(self, view: LookMLView) -> str:
        """Renders a starter curated refinement view."""
        template = self.jinja_env.get_template("curated_refinement.lkml.j2")
        rendered = template.render(view=view)
        try:
            lkml.load(rendered)
        except Exception as e:
            raise ValueError(f"Generated LookML refinement for {view.base_view_name} failed validation: {e}")
        return rendered

    def render_model(self, model_name: str, connection_name: str, views: List[LookMLView]) -> str:
        """Renders the LookML model file."""
        template = self.jinja_env.get_template("model.lkml.j2")
        rendered = template.render(
            model_name=model_name,
            connection_name=connection_name,
            views=views,
        )
        try:
            lkml.load(rendered)
        except Exception as e:
            raise ValueError(f"Generated model {model_name} failed validation: {e}")
        return rendered
