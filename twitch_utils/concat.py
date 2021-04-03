"""Usage: twitch_utils concat [options] <input>... (-o <name> | --output <name>)

This script concatenates two or more MPEG-TS files deleting
overlapping segments in the process. Re-encoding is not required.

Clips are aligned by start_time field. For example, Twitch
streams and their corresponding VODs share the same timeline.
It means that you can record stream, download its beginning
later and concatenate them by using this script.

Supported output formats:
  * mp4 (*.mp4)
  * mpegts (*.ts, -)
  * flv (*.flv, -)
  * txt (*.txt, -), map file for ffmpeg's concat demuxer

Options:
  -y, --force                     Overwrite output file without confirmation.
  -f <format>, --format=<format>  Force output pipe format. Has no effect if
                                  output file name is specified. [default: mpegts]
  -o <name>, --output=<name>      Name of the output file. Use '-' to output
                                  directly to stdout.

MP4 options:
  --faststart   Move moov atom to the front of the file. Requires second pass
                that makes concatenation much slower.
"""

import os
import sys

from docopt import docopt
from subprocess import run, PIPE

from .clip import Clip
from .utils import tmpfile


class TimelineMissingRangeError(Exception):
    def __init__(self, start, end):
        super().__init__(f'Range {start}~{end} is missing')

        self.start = start
        self.end = end


class Timeline(list):
    @staticmethod
    def find_clip(clips: list, pos: float) -> Clip:
        abs_start = clips[0].start
        end = None

        for clip in clips:
            if clip.start <= pos and clip.end > pos:
                return clip

            if clip.start > pos:
                end = clip.start

        raise TimelineMissingRangeError(pos - abs_start, end - abs_start)

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

            # Cut both segments in the middle of the overlap to avoid
            # losing or duplicating frames on the edges
            #                       outpoint   end
            # self[-2] ================|--------|
            #                 |--------|============ self[-1]
            #               start   inpoint
            middle = self[-1].start + (self[-2].end - self[-1].start) / 2
            self[-2].outpoint = self[-1].inpoint = middle

    def ffconcat_map(self) -> str:
        return '\n'.join([
            f"file '{c.path}'\ninpoint {c.inpoint}\noutpoint {c.outpoint}\n"
            for c in self
        ])

    def render(self, path: str = 'full.mp4', container: str = 'mp4',
               mp4_faststart: bool = False, force: bool = False) -> int:
        concat_map = self.ffconcat_map()

        if path.endswith('.txt') or path == '-' and container == 'txt':
            if path == '-':
                print(concat_map)
            else:
                with open(path, 'w') as fo:
                    fo.write(concat_map)
                    fo.flush()
            return 0

        print(concat_map, file=sys.stderr)

        map_file_name = tmpfile('txt', '.')
        map_file = open(map_file_name, 'w')
        map_file.write(concat_map)
        map_file.flush()
        map_file.close()

        command = ['ffmpeg']

        if force:
            command += ['-y']

        if path.endswith('.ts') or path == '-' and container == 'mpegts':
            command += ['-copyts']
        
        command += ['-f', 'concat', '-safe', '0', '-hide_banner',
                    '-i', map_file_name, '-c', 'copy']

        if path.endswith('.ts') or path == '-' and container == 'mpegts':
            command += ['-muxdelay', '0']
            if path == '-':
                command += ['-f', 'mpegts']
        elif path.endswith('.flv') or path == '-' and container == 'flv':
            command += ['-bsf:a', 'aac_adtstoasc']
            if path == '-':
                command += ['-f', 'flv']
        elif path.endswith('.mp4'):
            command += ['-fflags', '+genpts', '-async', '1']
            if mp4_faststart:
                command += ['-movflags', 'faststart']

        command += [path]

        p = run(command)

        os.unlink(map_file_name)

        return p.returncode


def main(argv=None):
    args = docopt(__doc__, argv=argv)

    try:
        timeline = Timeline([Clip(path) for path in args['<input>']])
    except TimelineMissingRangeError as ex:
        print(f'ERROR: Range {int(ex.start)}~{int(ex.end)} is not present in '
              'provided files')
        sys.exit(1)

    sys.exit(timeline.render(args['--output'],
                             container=args['--format'],
                             mp4_faststart=args['--faststart'],
                             force=args['--force']))


if __name__ == '__main__':
    main()
