import logging
import os
import uuid

logger = logging.getLogger(__name__)


def is_directory_writable(path: str) -> bool:
    """
    Determine whether a directory is writable.

    Creates the directory if it does not already exist and attempts
    to create and delete a temporary file within it to verify write
    permissions.

    Args:
        path (str): Path to the directory to check.

    Returns:
        - bool: True if the directory exists (or can be created) and is writable. False otherwise.
    """
    try:
        os.makedirs(path, exist_ok=True)

        test_file = os.path.join(path, f".write_test_{uuid.uuid4().hex}")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)

        return True

    except Exception as exc:
        logger.warning(
            f"No permission to write in directory or read-only filesystem. (Error: {exc})"
        )
        return False
