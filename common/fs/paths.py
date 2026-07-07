from pathlib import Path


def resolve_output_path(req_path: str | None, base_dir: str) -> str:
    """Resolve an output directory path relative to a base directory

    Args:
        req_path (str): Optional user-provided output path (absolute or relative)
        base_dir (str): Default base directory used when 'req_path' is missing
                  or when resolving relative paths

    Returns:
        - str: A normalized output directory path as a string
    """
    if not req_path:
        return base_dir

    path = Path(req_path)
    if path.is_absolute():
        return str(path)

    return str(Path(base_dir) / path)


def to_posix_path(path: str | Path) -> str:
    """Normalize a path to use forward slashes, adhering to platform-native parsing.

    Translates Windows backslashes to forward slashes, while preserving macOS/Linux
    literal backslashes in filenames.

    Args:
        path (str | Path): The path of the file

    Returns:
        - str: Path formatted as posix
    """
    return Path(path).as_posix()
