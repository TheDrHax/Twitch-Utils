import os
import tempfile


def tmpfile(ext='tmp', path=None):
    if not path:
        path = tempfile.gettempdir()
    return os.path.join(path, os.urandom(24).hex() + '.' + ext)