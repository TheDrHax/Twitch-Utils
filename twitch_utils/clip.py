import os
import json
import parselmouth as pm

from multiprocessing.pool import ThreadPool
from subprocess import run, PIPE
from tempfile import NamedTemporaryFile


class Clip(object):
    @staticmethod
    def clip_info(path: str) -> dict:
        command = ('ffprobe -v error -of json -show_entries '
                   'format=duration,start_time ' + path).split()
        proc = run(command, stdout=PIPE)
        return json.loads(proc.stdout)

    def __init__(self, path: str, tmpfile=None):
        self.name = os.path.basename(path)
        self.path = path
        self._tmpfile = tmpfile

        info = self.clip_info(path)['format']
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
            self._tmpfile.close()

    def slice(self, start: float, duration: float, chunks: int = 1,
              format: str = 'wav', ar: int = 200):
        """Split this Clip into one or multiple temporary Clips.

        By default splits only the audio track, outputting chunks
        in WAV format.
        """
        command = (f'ffmpeg -y -v error -ss {start} '
                   f'-i {self.path} -vn').split()

        if start > self.duration:
            return []

        results = []
        for i in range(chunks):
            chunk_end = start + duration * (i + 1)
            if self.duration < chunk_end:
                duration -= chunk_end - self.duration

            if duration <= 0:  # nothing left
                break

            tmp = NamedTemporaryFile()
            output = (f'-ar {ar} -f {format} -ss {duration * i} '
                      f'-t {duration} {tmp.name}').split()
            command.extend(output)
            results.append(tmp)

        if run(command).returncode != 0:
            [chunk.close() for chunk in results]
            raise Exception('ffmpeg exited with non-zero code')

        return [Clip(chunk.name, tmpfile=chunk) for chunk in results]

    def slice_generator(self, start: float, duration: float, **kwargs):
        pool = ThreadPool(1)
        kwargs['chunks'] = 1

        position = start
        async_result = None

        while position < self.duration:
            result = None

            if async_result:
                result = async_result.get()[0]
                position += duration

            async_result = pool.apply_async(self.slice, kwds=kwargs,
                                            args=(position, duration))

            if result:
                yield position - duration, result

        pool.close()

    def offset(self, clip: 'Clip') -> (float, float):
        """Find position of this Clip in another Clip (may be negative).
        
        Returns two values: offset in seconds and cross-correlation score.
        """
        s1, s2 = pm.Sound(self.path), pm.Sound(clip.path)
        cc = s1.cross_correlate(s2, pm.AmplitudeScaling.SUM)
        score = cc.values.max()
        frame = cc.values.argmax()
        offset = cc.frame_number_to_time(frame)
        return offset, score
