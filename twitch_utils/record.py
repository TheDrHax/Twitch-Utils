"""Usage: twitch_utils record [options] [--] <channel> [<quality>]

Parameters:
  channel       Name of the channel. Can be found in the URL: twitch.tv/<channel>
  quality       Resolution and framerate of the recording. To get all available
                values use `streamlink https://twitch.tv/<channel>`.

Options:
  -j <threads>  Number of simultaneous downloads of live segments. This option
                is passed to streamlink as --hls-segment-threads. [default: 4]
  -b <speed>    Bandwidth limit for both live stream and VOD in bytes per second.
                This option requires `pv` to work. Accepts suffixes K, M, G or T.
  --debug       Forward output of streamlink and ffmpeg to stderr.
"""

import os
import sys

from .clip import Clip
from .concat import Timeline
from time import sleep
from docopt import docopt
from subprocess import Popen, PIPE
from tcd.twitch import Channel
from multiprocessing import Process


DEBUG = False


class Stream(object):  
    def __init__(self, url: str, quality: str = 'best',
                 threads: int = 1, bandwidth: str = None):
        self.url = url
        self.quality = quality
        self.threads = threads
        self.bandwidth = bandwidth
    
    def _args(self) -> list:
        params = {'hls-timeout': 60,
                  'hls-segment-timeout': 60,
                  'hls-segment-attempts': 5,
                  'hls-segment-threads': self.threads}

        return [f'--{key}={value}' for key, value in params.items()]

    def download(self, dest: str):
        print(f'Downloading `{self.url}` into {dest}')

        sl_cmd = ['streamlink', '-l', 'debug']
        sl_cmd += self._args()
        sl_cmd += [self.url, self.quality, '-O']

        with open(dest, 'wb') as fo:
            sl_kwargs = {'stdout': PIPE if self.bandwidth else fo,
                         'stderr': sys.stderr if DEBUG else PIPE}
            sl_proc = Popen(sl_cmd, **sl_kwargs)

            if self.bandwidth:
                pv_cmd = ['pv', '-q', '-L', self.bandwidth]
                pv_kwargs = {'stdin': sl_proc.stdout,
                             'stdout': fo}
                pv_proc = Popen(pv_cmd, **pv_kwargs)

                sl_proc.wait()
                pv_proc.wait()
            else:
                sl_proc.wait()

            if sl_proc.returncode != 0:
                print(f'WARN: `{cmd}` exited with non-zero '
                      f'code ({sl_proc.returncode})')
                sys.exit(sl_proc.returncode)


def main(argv=None):
    args = docopt(__doc__, argv=argv)

    global DEBUG
    DEBUG = args['--debug']

    c = Channel(args['<channel>'])

    print(f'Checking if channel `{c.name}` is active...')

    try:
        v = c.live_vod()
    except KeyError:
        print(f'ERR: Channel `{c.name}` does not exist!')
        sys.exit(1)

    if not v:
        print(f'ERR: Channel is offline or live VOD is not ready yet!')
        sys.exit(1)
    
    print(f'Stream is online! Found the ID of live VOD: {v}')

    stream = Stream(f'https://twitch.tv/{c.name}',
                    bandwidth=args.get('-b', None),
                    quality=args.get('<quality>', 'best'),
                    threads=args['-j'])
    vod = Stream(f'https://twitch.tv/videos/{v}',
                 bandwidth=stream.bandwidth,
                 quality=stream.quality,
                 threads=1)

    p_stream = Process(target=stream.download, args=(f'{v}.end.ts',))
    p_vod = Process(target=vod.download, args=(f'{v}.start.ts',))

    print('Starting to record the live stream...')
    p_stream.start()

    sleep(600)
    print('Starting to download live VOD (beginning of the stream)...')
    p_vod.start()

    p_vod.join()
    p_stream.join()

    p_vod.close()
    p_stream.close()

    print('Download finished')
    print('Trying to assemble downloaded segments into full stream...')

    try:
        t = Timeline([Clip(f'{v}.{part}.ts') for part in ['start', 'end']])
    except Exception as ex:
        print(ex)
        print('ERR: Unable to concatenate segments!')
        sys.exit(1)

    print('Segments can be concatenated! Starting rendering...')
    t.render(sys.argv[2] if len(sys.argv) == 3 else f'{v}.mp4')

    print('Cleaning up...')
    [os.unlink(f'{v}.{part}.ts') for part in ['start', 'end']]


if __name__ == '__main__':
    main()
