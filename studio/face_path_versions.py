"""Name and select published Face Tracking sets without loading the detector."""
import re


INDEX_NAME = re.compile(r"Face Paths(?:\.v([1-9][0-9]*))?\.json", re.I)


def index_path(directory, version):
    return directory / ("Face Paths.json" if version == 1 else f"Face Paths.v{version}.json")


def camera_path(directory, media, version):
    return directory / f"{media.stem}__FacePath.v{version}.json"


def latest_index(directory):
    if directory.is_symlink():
        raise ValueError(f"Redirected Face Tracking folder: {directory}")
    candidates = []
    for path in directory.iterdir() if directory.is_dir() else ():
        match = INDEX_NAME.fullmatch(path.name)
        if match and (path.is_file() or path.is_symlink()):
            candidates.append((int(match[1] or 1), path))
    if not candidates:
        return None
    version = max(number for number, _ in candidates)
    paths = [path for number, path in candidates if number == version]
    if len(paths) != 1:
        raise ValueError(f"Ambiguous Face Tracking index version {version}: {paths}")
    path, = paths
    if path.is_symlink():
        raise ValueError(f"Redirected face path index: {path}")
    return version, path
