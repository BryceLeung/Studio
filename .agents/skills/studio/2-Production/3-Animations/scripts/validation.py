"""Read-only completion checks for human-approved animation MP4s."""
import importlib.util
from pathlib import Path
from scripts.validation import StageValidation


class Validation(StageValidation):
    def validate_stage(self):
        source = Path(__file__).with_name('animations.py')
        spec = importlib.util.spec_from_file_location('studio_animation_validation', source)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        helper.ROOT = self.workflow
        self.require_inventory('inputs', helper.registry_paths(self.project), exact=True)
        approved = helper.approval_inventory(self.project)
        self.require_inventory('outputs', [p for videos in approved.values() for p in videos], exact=True)
        if any(entry.get('method') != 'sha256' for entry in self.manifest.get('outputs', [])):
            raise ValueError('DONE lacks exact approved MP4 fingerprints')
