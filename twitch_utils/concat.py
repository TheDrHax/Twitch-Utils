"""Usage: twitch_utils concat <input>... (-o <name> | --output <name>)

This script concatenates two or more MPEG-TS files deleting
overlapping segments in the process. Re-encoding is not required.

Clips are aligned by start_time field. For example, Twitch
streams and their corresponding VODs share the same timeline.
It means that you can record stream, download its beginning
later and concatenate them by using this script.

Options:
  -o <name>, --output=<name>  Name of the output file. Use '-' to output MPEG-TS
                              directly to stdout.
"""

import os
import sys
import json

from docopt import docopt
from subprocess import run, PIPE
from tempfile import NamedTemporaryFile

from .clip import Clip


class Timeline(list):
    @staticmethod
    def find_clip(clips: list, pos: float) -> Clip:
        for clip in clips:
            if clip.start <= pos and clip.end > pos:
                return clip
        raise Exception(f'Position {pos} is not present in any of clips')

    def __init__(self, clips: list):
        clips.sort(key=lambda k: k.start)

        self.start = min([clip.start for clip in clips])
        self.end = max([clip.end for clip in clips])

        pos = self.start
        while pos < self.end:
            self.append(self.find_clip(clips, pos))
            pos = self[-1].end

            if len(self) < 2:
                continue

            # Cut previous segment to match next segment's start
            #                  outpoint       end
            # self[-2] ============|-----------|
            #                      |================ self[-1]
            #                 start=inpoint
            self[-2].outpoint = self[-1].inpoint

    def ffconcat_map(self) -> str:
        return '\n'.join([
            f'file {c.path}\ninpoint {c.inpoint}\noutpoint {c.outpoint}\n'
            for c in self
        ])

    def render(self, path: str = 'full.mp4') -> int:
        concat_map = self.ffconcat_map()
        print(concat_map, file=sys.stderr)

        map_file = NamedTemporaryFile('w', dir='.')
        map_file.write(concat_map)
        map_file.flush()

        command = ['ffmpeg']

        if path.endswith('.ts') or path == '-':
            command += ['-copyts']
        
        command += ['-f', 'concat', '-safe', '0', '-hide_banner',
                    '-i', map_file.name, '-c', 'copy']

        if path.endswith('.ts') or path == '-':
            command += ['-muxdelay', '0']
            if path == '-':
                command += ['-f', 'mpegts']
        elif path.endswith('.mp4'):
            command += ['-fflags', '+genpts', '-async', '1',
                        '-movflags', 'faststart']

        command += [path]

        p = run(command)

        map_file.close()

        return p.returncode


def main(argv=None):
    args = docopt(__doc__, argv=argv)

    timeline = Timeline([Clip(path) for path in args['<input>']])
    sys.exit(timeline.render(args['--output']))


if __name__ == '__main__':
    main()
