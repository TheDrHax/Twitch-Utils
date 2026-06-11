import os
import tempfile
from threading import Lock


def tmpfile(ext='tmp', path=None):
    if not path:
        path = tempfile.gettempdir()
    return os.path.join(path, os.urandom(24).hex() + '.' + ext)


class Counter:
    def __init__(self, value = 0):
        self._value = value
        self.lock = Lock()

    def set(self, value):
        with self.lock:
            self._value = value
            return self._value

    def inc(self):
        with self.lock:
            self._value += 1
            return self._value
    
    def dec(self):
        with self.lock:
            self._value -= 1
            return self._value

    @property
    def value(self):
        return self._value
