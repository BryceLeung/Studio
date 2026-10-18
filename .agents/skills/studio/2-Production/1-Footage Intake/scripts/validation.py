"""Read-only evidence and complete-group checks for Footage Intake."""
import importlib.util
from pathlib import Path
import sys

from scripts.validation import StageValidation


class Validation(StageValidation):
    def validate_stage(self):
        source = Path(__file__).with_name("validate-footage.py")
        spec = importlib.util.spec_from_file_location("studio_intake_validation_helper", source)
        helper = importlib.util.module_from_spec(spec)
        bytecode = sys.dont_write_bytecode
        try:
            sys.dont_write_bytecode = True
            spec.loader.exec_module(helper)
        finally:
            sys.dont_write_bytecode = bytecode
        approved_dir = self.confined(Path(*helper.APPROVED_PARTS))
        footage_dir = self.confined(Path(*helper.FOOTAGE_PARTS))
        if not approved_dir.is_dir() or not footage_dir.is_dir():
            raise ValueError("Approved scripts and footage folders must exist")
        approved = sorted(approved_dir.glob("*.docx"))
        if not approved or any(not path.is_file() for path in approved):
            raise ValueError("No usable approved scripts found")
        for path in approved:
            self.confined(path)
        media = []
        for path in footage_dir.iterdir():
            if path.is_file():
                self.confined(path)
                match = helper.FILENAME.fullmatch(path.name)
                if match and match["ext"].lower() in helper.ROLE_EXTENSIONS[match["role"]]:
                    media.append(path)
        complete, problems = helper.inspect_footage(footage_dir)
        missing = [path.stem for path in approved if path.stem not in complete]
        if missing or problems:
            raise ValueError(f"Incomplete approved-script footage: {missing}; problems: {problems}")
        self.require_inventory("inputs", approved, exact=True)
        self.require_inventory("outputs", media, exact=True)
