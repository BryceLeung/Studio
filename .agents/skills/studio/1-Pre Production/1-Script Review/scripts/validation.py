"""Require every approved filming script to use the shared three-column format."""
import re

from scripts.validation import StageValidation


class Validation(StageValidation):
    def validate_stage(self):
        from studio.script_docx import validate_three_column

        output = self.definition["outputs"][0]
        pattern = re.compile(output["filename_regex"], re.I)
        for script in sorted(self.confined(output["folder"]).iterdir()):
            if script.is_file() and pattern.fullmatch(script.name):
                validate_three_column(self.confined(script))
