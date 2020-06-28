import os
import json
import tempfile
import parselmouth as pm

from multiprocessing.pool import ThreadPool
from subprocess import run, PIPE


class Clip(object):
    @staticmethod
    def clip_info(path: str) -> dict:
        command = ('ffprobe -v error -of json -show_entries '
                   'format=duration,start_time ' + path).split()
        proc = run(command, stdout=PIPE)
        return json.loads(proc.stdout)

    def __init__(self, path: str, ar: int = 500,
                 container: str = 'wav', tmpfile = None):
        self.name = os.path.basename(path)
        self.path = path
        self._tmpfile = tmpfile

        self.ar = ar
        self.container = container

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
            os.unlink(self._tmpfile)

    def slice(self, start: float, duration: float, chunks: int = 1):
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

            tmp_file_name = os.path.join(tempfile.gettempdir(),
                                         os.urandom(24).hex())
            output = (f'-ar {self.ar} -f {self.container} '
                      f'-ss {duration * i} '
                      f'-t {duration} {tmp_file_name}').split()
            command.extend(output)
            results.append(tmp_file_name)

        if run(command).returncode != 0:
            [os.unlink(chunk) for chunk in results]
            raise Exception('ffmpeg exited with non-zero code')

        return [Clip(chunk,
                     tmpfile=chunk,
                     ar=self.ar,
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

    def offset(self, clip: 'Clip') -> (float, float):
        """Find position of this Clip in another Clip (may be negative).
        
        Returns two values: offset in seconds and cross-correlation score.
        """
        s1 = pm.Sound(self.path).convert_to_mono()
        s2 = pm.Sound(clip.path).convert_to_mono()
        cc = s1.cross_correlate(s2, pm.AmplitudeScaling.SUM)
        score = cc.values.max()
        frame = cc.values.argmax()
        offset = cc.frame_number_to_time(frame)
        return offset, score
