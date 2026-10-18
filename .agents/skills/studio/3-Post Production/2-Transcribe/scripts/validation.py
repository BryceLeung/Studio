"""Read-only transcript inventory, JSON, and stale-output guard checks."""
import json
from pathlib import Path
import re

from scripts import stage_completion as completion
from scripts.validation import StageValidation
from studio.fingerprints import matches, matches_identity


class Validation(StageValidation):
    def inventory(self):
        intake = self.confined(self.definition["inputs"][0]["folder"])
        directory = self.confined(self.definition["folder"])
        if not intake.is_dir() or not directory.is_dir():
            raise ValueError("Transcript and intake folders must exist")
        pattern = re.compile(self.definition["inputs"][0]["filename_regex"], re.I)
        wavs = sorted(path for path in intake.iterdir() if path.is_file() and pattern.fullmatch(path.name))
        if not wavs or any(self.confined(path).stat().st_size == 0 for path in wavs):
            raise ValueError("No complete non-empty intake lav WAV inventory")
        expected = {path.stem + self.definition["outputs"][0]["suffix"] for path in wavs}
        if len(expected) != len(wavs):
            raise ValueError("Multiple intake WAVs map to the same transcript name")
        outputs = sorted(path for path in directory.glob("*.json") if path.name != "Stale Transcripts.json")
        actual = {path.name for path in outputs}
        if actual != expected:
            raise ValueError(f"Transcript inventory differs from intake: missing={sorted(expected - actual)}, extra={sorted(actual - expected)}")
        for output in outputs:
            output = self.confined(output)
            if not output.is_file() or output.stat().st_size == 0:
                raise ValueError(f"Empty or missing transcript JSON: {output}")
            try:
                document = json.loads(output.read_text(encoding="utf-8-sig"))
            except (UnicodeError, ValueError) as error:
                raise ValueError(f"Invalid transcript JSON: {output}") from error
            if not isinstance(document, dict):
                raise ValueError(f"Transcript JSON must be an object: {output}")
        return wavs, outputs

    def historical_pairing(self, wavs, outputs):
        directory = self.confined(self.definition["folder"])
        if (directory / "DONE").is_symlink():
            raise ValueError("Redirected transcript DONE")
        marker = self.confined(directory / "DONE")
        if not marker.exists():
            if (directory / "STALE").is_symlink():
                raise ValueError("Redirected transcript DONE archive")
            archive = self.confined(directory / "STALE")
            if not archive.exists():
                return
            if not archive.is_dir():
                raise ValueError("Malformed transcript DONE archive")
            versions = []
            for slot in archive.iterdir():
                if slot.name.isdecimal():
                    if slot.is_symlink() or (slot / "DONE").is_symlink():
                        raise ValueError("Redirected archived transcript DONE")
                    slot = self.confined(slot)
                    saved = self.confined(slot / "DONE")
                    if slot.is_dir() and not saved.exists():
                        if (slot / "EVENT.json").is_symlink():
                            raise ValueError("Redirected transcript archive event")
                        event = self.confined(slot / "EVENT.json")
                        if event.is_file() and not event.is_symlink():
                            metadata = json.loads(event.read_text(encoding="utf-8-sig"))
                            if (isinstance(metadata, dict) and metadata.get("schema") == "studio.stale.event"
                                    and metadata.get("schema_version") == 1 and metadata.get("step") == "3.2"
                                    and metadata.get("had_done") is False):
                                continue
                    if not slot.is_dir() or not saved.is_file():
                        raise ValueError("Malformed archived transcript DONE")
                    versions.append((int(slot.name), saved))
            if not versions:
                return
            marker = max(versions)[1]
        baseline = json.loads(marker.read_text(encoding="utf-8-sig"))
        if not isinstance(baseline, dict) or baseline.get("step") not in (None, "3.2"):
            raise ValueError("Malformed previous transcript DONE")
        inventories = []
        for section in ("inputs", "outputs"):
            entries = baseline.get(section)
            if not isinstance(entries, list) or not entries:
                raise ValueError("Previous transcript DONE lacks usable provenance")
            inventory = self.definition[section][0]
            indexed = {}
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("scope") != "project":
                    raise ValueError("Malformed historical transcript fingerprint")
                path = completion.relative_path(entry["path"])
                self.confined(path)
                if (path.as_posix() != entry["path"] or path.parent.as_posix() != inventory["folder"]
                        or not re.fullmatch(inventory["filename_regex"], path.name, re.I)
                        or entry["path"] in indexed):
                    raise ValueError("Invalid historical transcript inventory")
                method = entry.get("method", "sha256" if "sha256" in entry else "legacy-media-stat")
                if method == "sha256":
                    if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-fA-F]{64}", entry["sha256"]):
                        raise ValueError("Malformed historical transcript hash")
                elif section == "inputs" and method in ("media-stat", "legacy-media-stat"):
                    values = ([entry.get("size_bytes"), entry.get("mtime_s")] if method == "media-stat"
                              else [entry.get("size_bytes", entry.get("bytes")), entry.get("mtime_ns")])
                    if any(type(value) is not int or value < 0 for value in values):
                        raise ValueError("Malformed historical audio identity")
                else:
                    raise ValueError("Invalid historical transcript fingerprint method")
                indexed[entry["path"]] = entry
            inventories.append(indexed)
        old_audio, old_json = inventories
        suffix = self.definition["outputs"][0]["suffix"]
        expected = {f"{self.definition['folder']}/{Path(path).stem}{suffix}" for path in old_audio}
        if expected != set(old_json) or len(expected) != len(old_audio):
            raise ValueError("Historical audio and transcript inventories do not pair")
        current_outputs = {path.name: path for path in outputs}
        for audio in wavs:
            source = audio.relative_to(self.project).as_posix()
            output = current_outputs[audio.stem + suffix]
            dest = output.relative_to(self.project).as_posix()
            if (source not in old_audio or not matches_identity(audio, old_audio[source])):
                if dest in old_json and matches(output, old_json[dest]["sha256"]):
                    raise ValueError(f"Audio changed; regenerate unchanged transcript JSON: {dest}")

    def validate_stage(self):
        wavs, outputs = self.inventory()
        directory = self.confined(self.definition["folder"])
        stale_path = self.confined(directory / "Stale Transcripts.json")
        if stale_path.exists():
            stale = json.loads(stale_path.read_text(encoding="utf-8-sig"))
            if not isinstance(stale, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                                  for key, value in stale.items()):
                raise ValueError("Malformed stale transcript guard")
            current = {path.relative_to(self.project).as_posix(): path for path in outputs}
            for path, digest in stale.items():
                self.confined(path)
                if path in current and matches(current[path], digest):
                    raise ValueError(f"Audio changed; regenerate unchanged transcript JSON: {path}")
        self.historical_pairing(wavs, outputs)
        self.require_inventory("inputs", wavs, exact=True)
        self.require_inventory("outputs", outputs, exact=True)
