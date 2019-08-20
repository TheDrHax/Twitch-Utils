"""Usage: twitch_utils offset [options] [--] FILE1 FILE2

This script matches one chunk from FILE1 against all chunks of
FILE2, returning offset of FILE1 and cross-correlation score.

Matching is performed by cross-correlation of audio tracks.
Video, subtitles and metadata will be ignored.

Options:
  -s <t>, --start <t>       Skip <t> seconds at the beggining of FILE2. [default: 0]
  -e <t>, --end <t>         Stop matching at this offset of FILE2.
  -t <t>, --split <t>       Split input to chunks of this length. [default: 300]
  -r <frequency>            WAV sampling frequency (lower is faster but less accurate). [default: 5000]

Exit conditions:
  --score-multiplier <N>    Stop computation if current score is at least N times bigger than
                            average of all scores calculated so far. [default: 4]

  --min-score <value>       Minimum cross-correlation score to be treated as potential match.
  --max-score <value>       Stop computation if cross-correlation score exceeds this value.

  WARNING: cross-correlation score depends on many factors such as segment
  length, audio sampling frequency and volume of the audio track. Be careful
  when using absolute values.

Output parameters:
  --round                   Output integer instead of float
  --score                   Output cross-correlation score along with offset

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
                end: float = None,
                chunk_size: float = 300,
                min_score: float = None,
                max_score: float = None,
                score_multiplier: float = 4,
                ar: int = 5000) -> (float, float):
    c1 = Clip(f1).slice(0, chunk_size, ar=ar)[0]
    c2 = Clip(f2)

    local_offset, local_score = 0, 0
    global_offset, global_score = 0, 0
    scores = []

    for position, chunk in c2.slice_generator(start, chunk_size, ar=ar):
        new_offset, new_score = c1.offset(chunk)
        scores.append(new_score)

        print(f'{position} / {c2.duration} | {new_offset} | {new_score}',
              file=sys.stderr)

        if min_score is not None and min_score > new_score:
            continue

        if max_score is not None and new_score >= max_score:
            break

        # calculate local and global maxima
        if len(scores) == 0 or \
           len(scores) == 1 and scores[-1] > local_score or \
           len(scores) >= 2 and scores[-1] > scores[-2]:
            local_score = new_score
            local_offset = position + new_offset

            if local_score > global_score:
                global_score = local_score
                global_offset = local_offset

        # detect a local maximum and stop if it matches exit conditions
        if len(scores) >= 2 and scores[-1] < scores[-2]:
            average = sum(scores) / len(scores)
            if scores[-2] / score_multiplier > average or \
               scores[-1] * score_multiplier < average:
                return local_offset, local_score

        if end is not None and position >= end:
            break

    return global_offset, global_score


def main(argv=None):
    args = docopt(__doc__, argv=argv)

    def get_arg(key, default_value, func=lambda x: x):
        if key in args and args[key] is not None:
            return func(args[key])
        else:
            return default_value

    kwargs = {
        'f1': args['FILE1'],
        'f2': args['FILE2'],
        'start': float(args['--start']),
        'end': get_arg('--end', None, float),
        'chunk_size': float(args['--split']),
        'min_score': get_arg('--min-score', None, float),
        'max_score': get_arg('--max-score', None, float),
        'score_multiplier': float(args['--score-multiplier']),
        'ar': int(args['-r'])
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
