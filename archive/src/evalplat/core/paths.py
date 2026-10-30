"""跨平台路径处理辅助函数。"""

from pathlib import PurePosixPath


def public_basename(value: object) -> str | None:
    """Return only a safe basename from a POSIX or Windows path.

    Persisted paths may have been produced on a different operating system
    than the one serving the API. Normalizing separators before extracting
    the basename keeps host directories out of public responses on both.
    """

    if value is None:
        return None
    text = str(value).replace("\\", "/")
    filename = PurePosixPath(text).name
    return filename if filename not in {"", ".", ".."} else None
