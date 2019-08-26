"""Usage: twitch_utils offset [options] [--] FILE1 FILE2

This script matches one chunk from FILE1 against all chunks of
FILE2, returning offset of FILE1 and cross-correlation score.

Matching is performed by cross-correlation of audio tracks.
Video, subtitles and metadata will be ignored.

Options:
  -s <t>, --start <t>       Skip <t> seconds at the beggining of FILE2. [default: 0]
  -e <t>, --end <t>         Stop matching at this offset of FILE2.
  -t <t>, --split <t>       Split FILE2 into chunks of this length. [default: 300]
  --template-start <t>      Template chunk will be cut from FILE1 starting at this offset. [default: 0]
  --template-duration <t>   Duration of template chunk. [default: 120]
  -r <frequency>            WAV sampling frequency (lower is faster but less accurate). [default: 1000]

Exit conditions:
  --score-multiplier <N>    Stop computation if local maximum score is at least
                            N times bigger than last local minimum. [default: 8]

  --min-score <value>       Minimum cross-correlation score to be treated as potential match.
                            This option is useful if input files have no collisions at all.
                            In this case both offset and score will be 0.
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


def find_offset(c1: Clip, c2: Clip,
                start: float = 0, end: float = None,
                chunk_size: float = 300,
                min_score: float = None,
                max_score: float = None,
                score_multiplier: float = 8) -> (float, float):
    if end is not None:
        c2.duration = end

    last_best_offset, last_best_score = 0, 0
    last_worst_score = 0
    best_offset, best_score = 0, 0
    prev_score = 0

    print(f'pos | offset | score | mul', file=sys.stderr)

    for position, chunk in c2.slice_generator(start, chunk_size):
        new_offset, new_score = c1.offset(chunk)

        delta = new_score - prev_score
        prev_score = new_score

        if new_score > best_score:
            best_score = new_score
            best_offset = position + new_offset

        if delta > 0:
            last_best_score = new_score
            last_best_offset = position + new_offset
        else:
            last_worst_score = new_score

        if last_worst_score != 0:
            cur_multiplier = round(last_best_score / last_worst_score, 2)
        else:
            cur_multiplier = 'N/A'

        print(f'{position} | {round(new_offset, 2)} | '
              f'{round(new_score, 2)} | '
              f'{cur_multiplier}',
              file=sys.stderr)

        if max_score is not None and new_score >= max_score:
            return new_offset, new_score

        if last_worst_score > 0 and last_best_score > 0:
            if last_worst_score * score_multiplier < last_best_score:
                return last_best_offset, last_best_score

    if min_score is None or best_score >= min_score:
        return best_offset, best_score
    else:
        return 0, 0


def main(argv=None):
    args = docopt(__doc__, argv=argv)

    def get_arg(key, default_value, func=lambda x: x):
        if key in args and args[key] is not None:
            return func(args[key])
        else:
            return default_value

    template_start = float(args['--template-start'])
    template_duration = float(args['--template-duration'])
    ar = int(args['-r'])

    c1 = Clip(args['FILE1'], ar=ar)

    if c1.duration < template_start or template_duration <= 0:
        raise Exception('Template is empty (check start offset and duration)')

    c1 = c1.slice(template_start, template_duration + template_start)[0]

    c2 = Clip(args['FILE2'], ar=ar)

    kwargs = {
        'start': float(args['--start']),
        'end': get_arg('--end', None, float),
        'chunk_size': float(args['--split']),
        'min_score': get_arg('--min-score', None, float),
        'max_score': get_arg('--max-score', None, float),
        'score_multiplier': float(args['--score-multiplier'])
    }

    offset, score = find_offset(c1, c2, **kwargs)

    offset -= template_start

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
