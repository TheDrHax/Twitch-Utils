import os
from dataclasses import dataclass
from retry_requests import retry
from typing import List, Union, BinaryIO
from time import time, sleep
from subprocess import DEVNULL, Popen, PIPE
from .utils import tmpfile


@dataclass
class HLSSegment:
    duration: float = 0
    name: str = '0.ts'
    init: bool = False
    final: bool = False


class SimpleHLS:
    def __init__(self, playlist: str) -> None:
        self.base_url = '/'.join(playlist.split('/')[:-1]) + '/'
        self.playlist = playlist.split('/')[-1]

        self.session = retry()
        self.segments: List[HLSSegment] = []
        self.last_reload = 0.0
        self.target_duration = 10.0
        self.reload()
    
    def reload(self):
        if len(self.segments) > 0:
            if time() < self.last_reload + self.target_duration / 2:
                sleep(self.target_duration / 2)

        self.last_reload = time()

        res = self.session.get(self.base_url + self.playlist)

        if res.status_code != 200:
            return

        segments: List[HLSSegment] = []
        segment = None

        for line in res.content.decode().splitlines():
            if line.startswith('#EXT-X-TARGETDURATION'):
                self.target_duration = float(line.split(':')[1])

            if line.startswith('#EXT-X-MAP:URI='):
                segment = HLSSegment()
                segment.name = line.split('=')[1].strip('"')
                segment.duration = 0
                segment.init = True

            if line.startswith('#EXTINF:'):
                if segment:
                    segments.append(segment)

                segment = HLSSegment()
                segment.duration = float(line[8:].split(',')[0])

            if not segment:
                continue

            if not line.startswith('#'):
                segment.name = line

            if line.startswith('#EXT-X-ENDLIST'):
                segment.final = True
                segments.append(segment)
                break
        
        self.segments = segments

    def iterate(self, start: float = 0, end: Union[float, None] = None):
        i = 0
        offset = 0

        while not end or offset < end:
            while len(self.segments) < i + 1:
                self.reload()

                if self.segments[i - 1].final:
                    raise StopIteration

            segment = self.segments[i]
            i += 1

            if segment.init:
                yield segment
                continue

            offset += segment.duration

            if offset < start:
                continue

            yield segment

            if segment.final:
                break

    def download(self, fo: BinaryIO, start: float = 0, end: Union[float, None] = None) -> bool:
        result = True

        ff_cmd = ['ffmpeg', '-hide_banner',
                  '-i', '-',
                  '-c', 'copy', '-copyts',
                  '-f', 'mpegts', '-']
        ff_kwargs = {'stdin': PIPE,
                     'stdout': fo,
                     'stderr': DEVNULL}
        ff_proc = Popen(ff_cmd, **ff_kwargs)

        for segment in self.iterate(start, end):
            res = self.session.get(self.base_url + segment.name, stream=True)

            if res.status_code != 200:
                print(f'Failed to download segment {segment.name} '
                      f'(code: {res.status_code})')
                result = False
                break

            for chunk in res.iter_content(4096):
                ff_proc.stdin.write(chunk)

        ff_proc.stdin.flush()
        ff_proc.stdin.close()
        ff_proc.wait()

        return result

    def offset(self):
        clip_file = tmpfile('ts')

        with open(clip_file) as fo:
            self.download(fo, end = 30)

        clip = Clip(clip_file)
        offset = clip.start

        os.unlink(clip_file)

        return offset


if __name__ == '__main__':
    import sys

    with open(sys.argv[2], 'wb') as fo:
        hls = SimpleHLS(sys.argv[1])
        hls.download(fo)
