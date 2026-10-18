"""Deck existence per approved source; no visual-inspection completion gate."""
from pathlib import Path
import re

from scripts.validation import StageValidation


class Validation(StageValidation):
    def validate_stage(self):
        source = self.definition["inputs"][0]
        target = self.definition["outputs"][0]
        pattern = re.compile(source["filename_regex"], re.I)
        scripts = [path for path in self.confined(source["folder"]).iterdir()
                   if path.is_file() and pattern.fullmatch(path.name)]
        folder = self.confined(target["folder"])
        decks = {path.name.casefold() for path in folder.iterdir()
                 if path.is_file() and re.fullmatch(target["filename_regex"], path.name, re.I)}
        missing = [str(Path(script.name).with_suffix(".pptx")) for script in scripts
                   if Path(script.name).with_suffix(".pptx").name.casefold() not in decks]
        if missing:
            raise ValueError(f"Missing cue-card decks: {sorted(missing)}")
