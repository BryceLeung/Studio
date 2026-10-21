"""Read-only B-roll content, current-request and full nested inventory validation."""
import importlib.util
from pathlib import Path
from scripts.validation import StageValidation


class Validation(StageValidation):
    def validate_stage(self):
        source = Path(__file__).with_name('broll.py')
        spec = importlib.util.spec_from_file_location('studio_broll_validation', source)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        helper.check(self.project)
        self.require_inventory('inputs', helper.registry_paths(self.project), exact=True)
        self.require_inventory('outputs', helper.inventory(self.project), exact=True)
