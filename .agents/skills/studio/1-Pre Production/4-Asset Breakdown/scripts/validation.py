"""Read-only Asset Breakdown completion checks."""
import importlib.util
from pathlib import Path

from scripts.validation import StageValidation


class Validation(StageValidation):
    def validate_stage(self):
        path = Path(__file__).with_name("assets.py")
        spec = importlib.util.spec_from_file_location("studio_asset_registry", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.check(self.project)
