from fastapi import HTTPException

from common.fs.permissions import is_directory_writable


def require_writable_directory(path: str) -> None:
    """
    Validate that a directory exists and is writable.

    Raises:
        HTTPException: If the directory cannot be created or is not writable.

    Used in API layer to enforce filesystem write permissions before
    executing operations that depend on output directories.
    """
    if not is_directory_writable(path):
        raise HTTPException(
            status_code=400,
            detail="No permission to write in directory or read-only filesystem.",
        )
