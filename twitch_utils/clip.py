import os
import json

from multiprocessing.pool import ThreadPool
from subprocess import Popen, run, PIPE
from typing import List, Tuple

from .utils import tmpfile


USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36')


class Clip(object):
    def ffprobe(self, entries, stream=None) -> dict:
        command = ['ffprobe']

        if self.path.startswith('http'):
            command += ['-user_agent', USER_AGENT]

        command += ['-v', 'error',
                   '-of', 'json', '-show_entries', entries]

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

        info = self.ffprobe('format=duration,start_time,format_name')['format']
        self.start = float(info.get('start_time', 0))

        duration = float(info.get('duration', 0))
        format_name = info.get('format_name', 'mpegts')

        if format_name == 'mov,mp4,m4a,3gp,3g2,mj2':
            self.end = duration
            self._duration = self.end - self.start
        else:
            self._duration = duration
            self.end = self.start + self.duration

        self.__duration = self._duration
        self.inpoint = self.start
        self.outpoint = self.end

        info = self.ffprobe('stream=id,codec_type,height')['streams']
        streams = dict(map(lambda s: (s['codec_type'], s), info))

        self.streams = [s[0] for s in streams.keys()]

        if 'video' in streams:
            self.height = streams['video']['height']
        else:
            self.height = 0

    def remux(self, streams = ['v', 'a', 'd']):
        fo = tmpfile('ts', '.')

        command = ['ffmpeg', '-y']

        if self.path.startswith('http'):
            command += ['-user_agent', USER_AGENT]

        command += ['-i', self.path,
                    '-c', 'copy',
                    '-copyts']

        for stream in streams:
            command += ['-map', f'0:{stream}?']

        command += [fo]

        ff = run(command)

        if ff.returncode != 0:
            os.unlink(fo)
            raise Exception(f'ffmpeg exited with non-zero code: {ff.returncode}')

        return Clip(fo, tmpfile=fo)

    def keyframes(self) -> Tuple[float, float, bool]:
        command = ['ffprobe']

        if self.path.startswith('http'):
            command += ['-user_agent', USER_AGENT]

        command += [
                   '-v', 'error',
                   '-of', 'csv',
                   '-show_frames', 
                   '-select_streams', 'v:0',
                   '-skip_frame', 'nokey',
                   '-show_entries', 'frame=pts_time',
                   self.path]

        ff = Popen(command, stdout=PIPE)
        
        frames = []
        for i, line in enumerate(ff.stdout):
            frames.append(float(line.decode().split(',')[1]))

            if i >= 2: break

        ff.terminate()
        ff.wait()

        if len(frames) < 3:
            print(f'WARN: Clip {self.name} is too short to determine '
                  'frame monotonicity')
            return 0, 0, False

        offset = frames[0]
        step = frames[1] - offset
        monotonous = (frames[2] - offset - step * 2) == 0

        return offset, step, monotonous

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

    def slice(self, start: float = 0, duration: float = None, chunks: int = 1,
              output_options: List[str] = []):
        """Split this Clip into one or multiple temporary Clips.

        By default splits only the audio track, outputting chunks
        in WAV format.
        """

        if not duration:
            duration = self.duration

        command = ['ffmpeg', '-y', '-v', 'error']

        if self.path.startswith('http'):
            command += ['-user_agent', USER_AGENT]

        command += ['-ss', f'{start}', '-i', self.path]

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

            try:
                yield result
            except GeneratorExit:
                break

        pool.close()
