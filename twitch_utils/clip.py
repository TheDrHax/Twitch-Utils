import os
import json

from multiprocessing.pool import ThreadPool
from subprocess import run, PIPE
from typing import List

from .utils import tmpfile


class Clip(object):
    def ffprobe(self, entries, stream=None) -> dict:
        command = ['ffprobe', '-v', 'error', '-of', 'json',
                   '-show_entries', entries]

        if stream:
            command += ['-select_streams', stream]

        command += [self.path]

        proc = run(command, stdout=PIPE)
        return json.loads(proc.stdout)

    def __init__(self, path: str, container: str = 'wav', tmpfile = None):
        self.name = os.path.basename(path)
        self.path = path
        self._tmpfile = tmpfile

        self.container = container

        info = self.ffprobe('format=duration,start_time')['format']
        if 'start_time' in info:  ## MPEG-TS only
            self.start = float(info['start_time'])
        else:
            self.start = 0

        self._duration = float(info['duration'])
        self.__duration = self._duration

        self.end = self.start + self.duration

        self.inpoint = self.start
        self.outpoint = self.end

    @property
    def duration(self):
        return self._duration

    @duration.setter
    def duration(self, new_value: float):
        if new_value <= self.__duration:
            self._duration = new_value

    def __del__(self):
        if self._tmpfile:
            os.unlink(self._tmpfile)

    def slice(self, start: float, duration: float, chunks: int = 1,
              output_options: List[str] = []):
        """Split this Clip into one or multiple temporary Clips.

        By default splits only the audio track, outputting chunks
        in WAV format.
        """
        command = (f'ffmpeg -y -v error -ss {start}').split()
        command += ['-i', self.path]

        if start > self.duration:
            return []

        results = []
        for i in range(chunks):
            chunk_end = start + duration * (i + 1)
            if self.duration < chunk_end:
                duration -= chunk_end - self.duration

            if duration <= 0:  # nothing left
                break

            tmp_file_name = tmpfile()
            output = (f'-f {self.container} '
                      f'-ss {duration * i} '
                      f'-t {duration}').split()
            output += output_options
            output += [tmp_file_name]
            command += output
            results += [tmp_file_name]

        if run(command).returncode != 0:
            [os.unlink(chunk) for chunk in results]
            raise Exception('ffmpeg exited with non-zero code')

        return [Clip(chunk,
                     tmpfile=chunk,
                     container=self.container)
                for chunk in results]

    def slice_generator(self, duration: float,
                        start: float = None, end: float = None,
                        reverse: bool = False, **kwargs):
        pool = ThreadPool(1)
        kwargs['chunks'] = 1

        if not start:
            start = 0

        if not end:
            end = self.duration

        if not reverse:
            position = start
        else:
            position = end - duration

        async_result = None

        while (position < end) if not reverse else (position > start):
            result = None

            if async_result:
                result = position, async_result.get()[0]

                if not reverse:
                    position += duration
                else:
                    position -= duration

                    if position < start:
                        duration -= start - position
                        position = start

            async_result = pool.apply_async(self.slice, kwds=kwargs,
                                            args=(position, duration))

            if not result:
                continue

            yield result

        pool.close()
