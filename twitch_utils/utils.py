import os
import tempfile


def tmpfile(ext='tmp'):
    return os.path.join(tempfile.gettempdir(),
                        os.urandom(24).hex() + '.' + ext)