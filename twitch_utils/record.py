"""Usage: twitch_utils record [options] --oauth=<token> [--] <channel> [<quality>]

Parameters:
  channel       Name of the channel. Can be found in the URL: twitch.tv/<channel>
  quality       Resolution and framerate of the recording. To get all available
                values use `streamlink https://twitch.tv/<channel>`.

Options:
  --oauth <token>   Twitch OAuth token. You need to extract it from the site's
                    cookie named "auth-token".
  -j <threads>      Number of simultaneous downloads of live segments. This option
                    is passed to streamlink as --hls-segment-threads. [default: 4]
  --debug           Forward output of streamlink and ffmpeg to stderr.
"""

import os
import sys

from time import sleep
from docopt import docopt
import dateutil.parser as dateparser
from subprocess import Popen, PIPE
from multiprocessing import Process

from .clip import Clip
from .concat import Timeline
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
