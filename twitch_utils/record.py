"""Usage: twitch_utils record [options] --oauth=<token> [--] <channel> [<quality>] [-o <file>]

Parameters:
  channel       Name of the channel. Can be found in the URL: twitch.tv/<channel>
  quality       Resolution and framerate of the recording. To get all available
                values use `streamlink https://twitch.tv/<channel>`.

Options:
  --oauth <token>   Twitch OAuth token. You need to extract it from the site's
                    cookie named "auth-token".
  -o <name>         Name of the output file. For more information see
                    `twitch_utils concat --help`.
  -j <threads>      Number of simultaneous downloads of live segments. [default: 4]
  -y, --force       Overwrite output file without confirmation.
  --debug           Forward output of streamlink and ffmpeg to stderr.
"""

import os
import sys

try:
    import streamlink
except ImportError:
    print('Error: You need to install tdh-twitch-utils[record] or '
          'tdh-twitch-utils[all] to use this feature.',
          file=sys.stderr)
    sys.exit(1)

from time import sleep
from docopt import docopt
import dateutil.parser as dateparser
from subprocess import Popen, PIPE
from multiprocessing import Process

from .clip import Clip
from .concat import Timeline, TimelineError
from .twitch import TwitchAPI


DEBUG = False


class Stream(object):  
    def __init__(self, url: str, quality: str = 'best',
                 threads: int = 1, oauth: str = None):
        self.url = url
        self.quality = quality
        self.threads = threads
        self.oauth = oauth
    
    def _args(self) -> list:
        params = {'hls-timeout': 60,
                  'hls-segment-timeout': 60,
                  'hls-segment-attempts': 5,
                  'hls-segment-threads': self.threads}

        if self.oauth:
            params['twitch-oauth-token'] = self.oauth

        return [f'--{key}={value}' for key, value in params.items()]

    def download(self, dest: str):
        print(f'Downloading `{self.url}` into {dest}')

        sl_cmd = ['streamlink', '-l', 'debug']
        sl_cmd += self._args()
        sl_cmd += [self.url, self.quality, '-O']

        with open(dest, 'wb') as fo:
            sl_kwargs = {'stdout': fo,
                         'stderr': sys.stderr if DEBUG else PIPE}
            sl_proc = Popen(sl_cmd, **sl_kwargs)
            sl_proc.wait()

            if sl_proc.returncode != 0:
                print(f'WARN: `{sl_cmd}` exited with non-zero '
                      f'code ({sl_proc.returncode})')
                sys.exit(sl_proc.returncode)


def main(argv=None):
    args = docopt(__doc__, argv=argv)

    global DEBUG
    DEBUG = args['--debug']

    output = args['-o']

    if output == '-':
        print('ERR: This script does not support stdout as output.')
        sys.exit(1)

    api = TwitchAPI(args['--oauth'])

    channel = args['<channel>']

    print(f'Checking if channel `{channel}` is active...')

    status = api.helix('streams', user_login=channel)['data']

    if not status:
        print(f'ERR: Channel is offline')
        sys.exit(1)
    else:
        status = status[0]

    print(f'Attempting to find ID of the live VOD...')

    vods = api.helix('videos', user_id=status['user_id'],
                     first=1, type='archive')['data']
    
    if len(vods) == 0:
        print(f'ERR: No VODs found on channel')
        sys.exit(1)

    stream_date = dateparser.isoparse(status['started_at'])
    vod_date = dateparser.isoparse(vods[0]['created_at']) 

    if vod_date < stream_date:
        print(f'ERR: Live VOD is not available yet')
        sys.exit(1)

    v = vods[0]["id"]

    stream = Stream(f'https://twitch.tv/{channel}',
                    quality=args.get('<quality>') or 'best',
                    threads=args['-j'])

    vod = Stream(f'https://twitch.tv/videos/{v}',
                 quality=stream.quality,
                 threads=1)

    print('Starting to record the live stream...')
    p_stream = Process(target=stream.download, args=(f'{v}.end.ts',))
    p_stream.start()

    sleep(60)
    print('Starting to download live VOD (beginning of the stream)...')
    for i in range(3):
        p_vod = Process(target=vod.download, args=(f'{v}.start.ts',))
        p_vod.start()
        p_vod.join()

        print('Testing the possibility of concatenation')
        try:
            Timeline([Clip(f'{v}.{part}.ts') for part in ['start', 'end']])
        except TimelineError as ex:
            if i == 2:
                print(ex)
                print('ERR: Unable to download live VOD')
                sys.exit(1)

            print('Concatenation is not possible, redownloading live VOD...')
            sleep(60)
            p_vod.close()

        print('Concatenation is possible, waiting for stream to end')
        p_vod.close()
        break

    p_stream.join()
    p_stream.close()

    print('Stream ended')

    try:
        t = Timeline([Clip(f'{v}.{part}.ts') for part in ['start', 'end']])
    except TimelineError as ex:
        print(ex)
        print('ERR: Unable to concatenate segments!')
        sys.exit(1)

    if not output:
        output = f'{v}.ts'

    print(f'Writing stream recording to {output}')
    t.render(output, force=args['--force'])

    print('Cleaning up...')
    [os.unlink(f'{v}.{part}.ts') for part in ['start', 'end']]


if __name__ == '__main__':
    main()
