"""Read-only animation approval, provenance and exact nested inventory checks."""
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
        helper.RENDERER = self.workflow / helper.LOCAL / 'renderer'
        helper.check(self.project)
        self.require_inventory('inputs', helper.registry_paths(self.project), exact=True)
        self.require_inventory('outputs', helper.inventory(self.project), exact=True)
        saved = {entry['path'] for entry in self.manifest.get('dependencies', [])
                 if entry.get('scope') == 'workflow'}
        expected = {(helper.LOCAL / 'renderer' / path).as_posix()
                    for path in helper.renderer_identity()}
        if not expected.issubset(saved):
            raise ValueError('DONE lacks current renderer inventory fingerprints')
