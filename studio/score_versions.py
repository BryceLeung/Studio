"""Numbered Take Selection artifacts shared by the scoring and editing stages."""
import re


STAGE = "3-Post Production/4-Take Selection"
NAME = re.compile(r"(.+)__Scores\.v([1-9][0-9]*)\.json", re.I)


def latest_scores(directory):
    """Select the highest numeric score version per case-insensitive script stem."""
    if directory.is_symlink():
        raise ValueError(f"Redirected Take Selection folder: {directory}")
    versions = {}
    for path in directory.iterdir() if directory.is_dir() else ():
        match = NAME.fullmatch(path.name)
        if match and (path.is_file() or path.is_symlink()):
            versions.setdefault(match[1].casefold(), []).append((int(match[2]), path))
    latest = {}
    for stem, candidates in versions.items():
        version = max(n for n, _ in candidates)
        paths = [path for n, path in candidates if n == version]
        if len(paths) != 1:
            raise ValueError(f"Ambiguous latest score version for {stem}: {paths}")
        path, = paths
        if path.is_symlink():
            raise ValueError(f"Redirected latest score file: {path}")
        latest[stem] = version, path
    return latest
