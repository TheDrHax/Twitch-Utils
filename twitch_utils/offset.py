"""Usage: twitch_utils offset [options] [--] FILE1 FILE2

This script matches one chunk from FILE1 against all chunks of
FILE2, returning offset of FILE1 and cross-correlation score.

Matching is performed by cross-correlation of audio tracks.
Video, subtitles and metadata will be ignored.

Options:
  -s <t>, --start <t>       skip <t> seconds at the beggining of both files [default: 0]
  -e <t>, --end <t>         stop matching at this offset of FILE2
  -c <t>, --chunk-size <t>  split input to chunks of this length [default: 60]
  -t <threshold>            stop when cross-correlation score is greater than
                            or equal to this value [default: 1500]

Output parameters:
  --round                   output integer instead of float
  --score                   output cross-correlation score along with offset

Usage examples:

  - Two video files

  offset.py template.mp4 long_video.mp4

  - Match beginning of YouTube video with local file

  offset.py $(youtube-dl -gf best VIDEO_ID) long_video.mp4
"""

import os
import sys

from docopt import docopt

from .clip import Clip


def find_offset(f1: str, f2: str,
                start: float = 0,
                end: float = 0,
                chunk_size: float = 60,
                threshold: int = 1500) -> (float, float):
    c1 = Clip(f1).slice(start, chunk_size + start)[0]
    c2 = Clip(f2)

    position = start
    offset, score = 0, 0
    while position < c2.duration:
        chunk = c2.slice(position, chunk_size)[0]
        new_offset, new_score = c1.offset(chunk)

        print(f'{position} / {c2.duration} | {new_offset} | {new_score}',
              file=sys.stderr)

        if new_score > score:
            score = new_score
            offset = position + new_offset - start
        
        if threshold != 0 and score >= threshold:
            break

        if end != 0 and position >= end:
            break

        position += chunk_size

    return offset, score


def main(argv=None):
    args = docopt(__doc__, argv=argv)

    kwargs = {
        'f1': args['FILE1'],
        'f2': args['FILE2'],
        'start': float(args['--start']),
        'end': float(args['--end']) if args['--end'] else 0,
        'chunk_size': float(args['--chunk-size']),
        'threshold': float(args['-t'])
    }

    offset, score = find_offset(**kwargs)
    
    if args['--round']:
        offset = round(offset)
        score = round(score)

    if args['--score']:
        print(f'Offset: {offset}')
        print(f'Score: {score}')
    else:
        print(offset)


if __name__ == '__main__':
    main()
