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
  --template-duration <t>   Duration of template chunk. [default: 60]
  -r <frequency>            WAV sampling frequency (lower is faster but less accurate). [default: 5000]

Exit conditions:
  --score-multiplier <N>    Stop computation if current score is at least N times bigger than
                            average of all scores calculated so far. [default: 4]

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


def find_offset(f1: str, f2: str,
                start: float = 0, end: float = None,
                chunk_size: float = 300,
                template_start: float = 0,
                template_duration: float = 60,
                min_score: float = None,
                max_score: float = None,
                score_multiplier: float = 4,
                ar: int = 5000) -> (float, float):
    c1 = Clip(f1)

    if c1.duration < template_start or template_duration <= 0:
        raise Exception('Template is empty (check start offset and duration)')

    c1 = c1.slice(template_start, template_duration + template_start, ar=ar)[0]

    c2 = Clip(f2)

    if end is not None:
        c2.duration = end

    offset, score = 0, 0
    scores = []

    for position, chunk in c2.slice_generator(start, chunk_size, ar=ar):
        new_offset, new_score = c1.offset(chunk)
        scores.append(new_score)

        print(f'{position} / {c2.duration} | {new_offset} | {new_score}',
              file=sys.stderr)

        if max_score is not None and new_score >= max_score:
            return new_offset - template_start, new_score

        # calculate local and global maxima
        if len(scores) == 0 or \
           len(scores) == 1 and scores[-1] > score or \
           len(scores) >= 2 and scores[-1] > scores[-2]:
            score = new_score
            offset = position + new_offset

        # detect a local maximum and stop if it matches exit conditions
        if len(scores) >= 2 and scores[-1] < scores[-2]:
            if min_score is not None and scores[-2] < min_score:
                continue

            average = sum(scores) / len(scores)           
            prev_score = scores[-2] / score_multiplier
            curr_score = scores[-1] * score_multiplier

            if not prev_score < average < curr_score:
                return offset - template_start, score

    return 0, 0


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
        'template_start': float(args['--template-start']),
        'template_duration': float(args['--template-duration']),
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
