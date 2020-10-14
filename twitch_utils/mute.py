"""Usage: twitch_utils mute [options] <input> <range>... (-o <name>)

This script attempts to separate streamer's voice from background music by
using Spleeter. Only specified time ranges are affected. Output contains the
same video, but without music in these parts.

The main purpose of this script is to remove automated Content-ID claims from
the video on YouTube. YouTube displays exact time ranges for every claim.

Timestamp format: [[HH:]MM:]SS[.MMM]
Time range format: START~END

Options:
  -o <file>   Name of the output file.
  --inverse   Remove voice instead of music.
  --pass <n>  Number of passes for each time range. [default: 1]
"""

import os
import sys

from docopt import docopt
from subprocess import run

try:
    from spleeter.separator import Separator
    from spleeter.audio.adapter import get_default_audio_adapter
except ImportError:
    print('Error: You need to install tdh-twitch-utils[mute] or '
          'tdh-twitch-utils[all] to use this feature.',
          file=sys.stderr)
    sys.exit(1)

from .clip import Clip
from .utils import tmpfile


def ptime(t: str) -> float:
    parts = list(map(float, t.split(':')))[::-1]
    return sum(part * 60**i for i, part in enumerate(parts))


def main(argv=None):
    args = docopt(__doc__, argv=argv)
    
    fi = Clip(args['<input>'])
    fo = args['-o']
    ranges = list(tuple(ptime(t) for t in range.split('~'))
                  for range in args['<range>'])

    loader = get_default_audio_adapter()
    sample_rate = 44100
    separator = Separator('spleeter:2stems')

    segments = {}

    for start, end in ranges:
        print(f'Processing range {start}-{end}...')

        options = ['-vn', '-r', str(sample_rate), '-f', 'wav']
        clip = fi.slice(start, end - start, output_options=options)[0]
        
        for i in range(int(args['--pass'])):
            waveform, _ = loader.load(clip.path, sample_rate=sample_rate)
            prediction = separator.separate(waveform)

            output = tmpfile('wav')

            target = 'accompaniment' if args['--inverse'] else 'vocals'
            loader.save(output, prediction[target], sample_rate)

            clip = Clip(output, tmpfile=output)

        segments[start] = clip

    print('Writing output file...')

    # Mute ranges in the original audio track
    # asetnsamples is required, source: https://superuser.com/a/1230890
    filters = '[0:a]asetnsamples=8192,'
    filters += ','.join(f"volume=0:enable='between(t,{start},{end})'"
                        for start, end in ranges)
    filters += '[main]'

    # Delay processed segments
    for i, (start, end) in enumerate(ranges):
        delay = int(start * 1000)
        filters += f';[{i+1}]'
        filters += f'asetnsamples=8192'
        filters += f',adelay={delay}|{delay},apad[delay{i+1}]'

    # Mix muted original track and all processed segments
    filters += ';[main]'
    for i, (start, end) in enumerate(ranges):
        filters += f'[delay{i+1}]'
    filters += f'amix=inputs={len(ranges) + 1}:duration=first'
    filters += f',volume={len(ranges) + 1}'

    filters += '[audio]'

    command = ['ffmpeg', '-i', fi.path]

    for start, segment in segments.items():
        command += ['-i', segment.path]

    # Copy codecs from the original video
    ainfo = fi.ffprobe('stream=codec_name,bit_rate', 'a')['streams'][0]
    command += ['-c:v', 'copy',
                '-c:a', ainfo['codec_name'],
                '-b:a', ainfo['bit_rate'],
                '-strict', '-2']

    command += ['-filter_complex', filters,
                '-map', '0:v', '-map', '[audio]', fo]

    if run(command).returncode != 0:
        if os.path.exists(fo):
            os.unlink(fo)
        raise Exception('ffmpeg exited with non-zero code')

if __name__ == '__main__':
    main()