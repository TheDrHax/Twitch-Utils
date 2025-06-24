from dataclasses import dataclass
from retry_requests import retry
from typing import List, Union
from time import time, sleep


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

    def download(self, filename: str, start: float = 0, end: Union[float, None] = None):
        with open(filename, 'wb') as fo:
            for segment in self.iterate(start, end):
                res = self.session.get(self.base_url + segment.name, stream=True)

                if res.status_code != 200:
                    print(f'Failed to download segment {segment.name} '
                          f'(code: {res.status_code})')
                    break

                for chunk in res.iter_content(4096):
                    fo.write(chunk)
