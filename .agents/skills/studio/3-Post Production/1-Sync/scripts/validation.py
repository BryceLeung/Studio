"""Validate existing sync results without installing, probing, or generating."""
import importlib.util
import json
from pathlib import Path
import sys

from scripts.validation import StageValidation
from scripts.stage_completion import require_idle_jobs


def read_helpers():
    """Import without leaving path/module changes or writing helper bytecode."""
    if sys.version_info < (3, 10):
        raise NotImplementedError("Sync validation requires Python 3.10+ to import its existing generator")
    previous_path, bytecode = sys.path[:], sys.dont_write_bytecode
    previous_installer = sys.modules.get("install_dependencies")
    try:
        sys.dont_write_bytecode = True
        modules = []
        for name in ("install_dependencies", "generate_offsets"):
            spec = importlib.util.spec_from_file_location(
                "studio_sync_validation_" + name, Path(__file__).with_name(name + ".py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules.append(module)
            if name == "install_dependencies":
                sys.modules[name] = module
        return modules[1], modules[0]
    finally:
        sys.path[:] = previous_path
        sys.dont_write_bytecode = bytecode
        if previous_installer is None:
            sys.modules.pop("install_dependencies", None)
        else:
            sys.modules["install_dependencies"] = previous_installer


class Validation(StageValidation):
    def validate_stage(self):
        helper, pins = read_helpers()
        footage = self.confined(Path(*helper.FOOTAGE_PARTS))
        destination = self.confined(Path(*helper.OUTPUT_PARTS))
        if not footage.is_dir() or not destination.is_dir():
            raise ValueError("Sync footage and output folders must exist")
        require_idle_jobs(destination)
        for path in footage.iterdir():
            if path.is_file():
                self.confined(path)
        groups = helper.discover_shots(footage, None)
        outputs = sorted(destination.glob("*.json"))
        expected_names = {f"{shot}.json" for shot in groups}
        if {path.name for path in outputs} != expected_names:
            raise ValueError("Sync JSON inventory differs from recording groups")
        inputs = []
        for shot, files in groups.items():
            output = self.confined(destination / f"{shot}.json")
            document = json.loads(output.read_text(encoding="utf-8"))
            generator = document.get("generator") if isinstance(document, dict) else None
            dependencies = generator.get("dependencies") if isinstance(generator, dict) else None
            required = {"python_version", "audio_offset_finder_version", "audio_offset_finder_revision",
                        "imageio_ffmpeg_version", "numpy_version", "scipy_version", "librosa_version",
                        "ffmpeg_version"}
            if (not isinstance(dependencies, dict) or set(dependencies) != required
                    or any(not isinstance(value, str) or not value for value in dependencies.values())
                    or dependencies["audio_offset_finder_revision"] != pins.BBC_REVISION
                    or dependencies["imageio_ffmpeg_version"] != pins.IMAGEIO_FFMPEG_VERSION):
                raise ValueError(f"Invalid or stale sync dependency identity: {shot}")
            expected = {
                "schema_version": 1, "shot": shot, "reference_role": "camA",
                "offset_convention": helper.OFFSET_CONVENTION,
                "generator": helper.generator_identity(dependencies),
                "clips": {role: helper.file_identity(files[role], self.project) for role in helper.ROLES},
            }
            if not helper.reusable(output, expected):
                raise ValueError(f"Stale or invalid sync JSON: {shot}")
            inputs.extend(files.values())
        self.require_inventory("inputs", inputs, exact=True)
        self.require_inventory("outputs", outputs, exact=True)
