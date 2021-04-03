"""Usage:
    twitch_utils record [options] --oauth=<token> <channel>
    twitch_utils record [options] [--oauth=<token>] <channel> <vod>

Parameters:
  channel   Name of the channel. Can be found in the URL: twitch.tv/<channel>
  vod       VOD of stream that is currently live. Can be found in the URL:
            twitch.tv/videos/<vod>

Options:
  --quality <value> Choose stream quality to save. Accepts the same values as
                    streamlink. To check available options use command
                    `streamlink twitch.tv/<channel>`. [default: best]
  --oauth <token>   Twitch OAuth token. You need to extract it from the site's
                    cookie named "auth-token".
  -o <name>         Name of the output file. For more information see
                    `twitch_utils concat --help`. Defaults to `<vod>.ts`.
  -j <threads>      Number of concurrent downloads of live segments. [default: 4]
  -y, --force       Overwrite output file without confirmation.
  --debug           Forward output of streamlink and ffmpeg to stderr.
"""

import os
import sys
import math
import itertools

try:
    import streamlink
except ImportError:
    print('Error: You need to install tdh-twitch-utils[record] or '
          'tdh-twitch-utils[all] to use this feature.',
          file=sys.stderr)
    sys.exit(1)

from time import sleep
from docopt import docopt
from parse import compile
import dateutil.parser as dateparser
from subprocess import Popen, PIPE
from multiprocessing import Process

from .clip import Clip
from .concat import Timeline, TimelineMissingRangeError
from .twitch import TwitchAPI


DEBUG = False


class Stream(object):
    PARSE_QUEUED = compile('{} Adding segment {segment:d} to queue{}')
    PARSE_COMPLETE = compile('{} Download of segment {segment:d} complete{}')

    def __init__(self, url: str,
                 quality: str = 'best',
                 threads: int = 1,
                 oauth: str = None,
                 start: int = 0,
                 end: int = None):
        self.url = url
        self.quality = quality
        self.threads = threads
        self.oauth = oauth
        self.start = start
        self.end = end

    def copy(self):
        return Stream(self.url, self.quality, self.threads,
                      self.oauth, self.start, self.end)

    def _args(self) -> list:
        params = {'hls-timeout': 60,
                  'hls-segment-timeout': 60,
                  'hls-segment-attempts': 5,
                  'hls-segment-threads': self.threads}

        if self.oauth:
            params['twitch-oauth-token'] = self.oauth

        if self.start > 0:
            params['hls-start-offset'] = math.floor(self.start)

        if self.end:
            params['hls-duration'] = math.ceil(self.end - self.start)

        args = ['-l', 'debug', '--twitch-disable-ads']
        args += [f'--{key}={value}' for key, value in params.items()]
        args += [self.url, self.quality, '-O']

        return args

    def download(self, dest: str) -> bool:
        """Exit codes: 0 - success, 1 - should retry, 2 - should stop"""

        print(f'Downloading `{self.url}` into {dest}')
        exit_code = 0

        fo = open(dest, 'wb')
        sl_cmd = ['streamlink'] + self._args()
        sl_kwargs = {'stdout': fo,
                     'stderr': PIPE,
                     'text': True}
        sl_proc = Popen(sl_cmd, **sl_kwargs)

        expected, downloaded = [-1] * 2

        while True:
            line = sl_proc.stderr.readline()

            if not line:
                break

            if DEBUG:
                print(line.rstrip(), file=sys.stderr)

            queued = Stream.PARSE_QUEUED.parse(line)
            complete = Stream.PARSE_COMPLETE.parse(line)

            if queued:
                segment = queued['segment']

                if queued['segment'] > expected:
                    expected = queued['segment']
            elif complete:
                segment = complete['segment']
                print(f'Downloaded segment {segment} out of {expected}')

                if downloaded == -1 or downloaded + 1 == segment:
                    downloaded = segment
                else:
                    print(f'ERR: Skipped segment {downloaded + 1}')
                    sl_proc.terminate()
                    exit_code = 1
                    break
            elif 'Thread-TwitchHLSStreamWriter' in line:
                print(f'ERR: {line}')
                sl_proc.terminate()
                exit_code = 1
                break
            elif 'No playable streams found' in line:
                print('WARN: Stream appears to be offline')
                sl_proc.terminate()
                exit_code = 2
                break
            elif 'Waiting for pre-roll ads' in line:
                print('Waiting for ads to finish...')

        sl_proc.wait()

        fo.flush()
        fo.close()

        return exit_code

    def _target_download(self, *args):
        result = self.download(*args)
        sys.exit(result)

    def download_async(self, dest: str) -> Process:
        p = Process(target=self._target_download, args=(dest,))
        p.start()
        return p


def generate_filename(vod_id, part):
    return f'{vod_id}.{part}.ts'


def create_timeline(vod_id, parts):
    clips = []

    for part in range(parts):
        filename = generate_filename(vod_id, part)

        try:
            clips.append(Clip(filename))
        except Exception:
            print(f'WARN: Clip {filename} is corrupted, ignoring...')

    return Timeline(clips)


def record(vod_id: str, stream: Stream, vod: Stream, parts: int = 0) -> int:
    resumed = parts > 0
    stream_result = -1
    missing_part = None

    while stream_result != 0:
        if parts == 0:
            print('Starting download of live stream')
        else:
            print('Resuming download of live stream')

        stream_proc = stream.download_async(generate_filename(vod_id, parts))
        parts += 1

        should_break = False
        for i in range(60):
            if stream_proc.exitcode == 2:
                if parts == 1:
                    sys.exit(1)
                else:
                    should_break = True
                    break
            sleep(1)

        if should_break:
            break

        if parts == 1:
            print('Starting download of VOD')

            vod_proc = vod.download_async(generate_filename(vod_id, parts))
            parts += 1

            vod_proc.join()
            vod_result = vod_proc.exitcode
            vod_proc.close()

            print(f'Finished download of VOD (exit code: {vod_result})')

        first_vod = parts == 2

        while missing_part or first_vod or resumed:
            # Avoid infinite loops
            resumed = False
            first_vod = False

            print('Testing the possibility of concatenation')

            try:
                create_timeline(vod_id, parts)
                print('Timeline is complete, good!')
                missing_part = None
                break
            except TimelineMissingRangeError as ex:
                missing_part = (ex.start, ex.end)
                print(f'WARN: Missing segment {ex.start}~{ex.end}, '
                      'retrying in 60 seconds...')
                sleep(60)

            print(f'Downloading segment {missing_part[0]}~{missing_part[1]}')

            segment = vod.copy()
            segment.start = max(0, missing_part[0] - 60)
            segment.end = missing_part[1] + 60

            segment = segment.download_async(generate_filename(vod_id, parts))
            parts += 1

            vod_proc.join()
            vod_proc.close()

        stream_proc.join()
        stream_result = stream_proc.exitcode
        stream_proc.close()

        print(f'Finished download of live stream (exit code: {stream_result})')

        if stream_result != 0:
            print('Resuming in 60 seconds...')
            sleep(60)

    print('All parts are downloaded!')
    return parts


def find_vod(oauth: str, channel: str) -> str:
    api = TwitchAPI(oauth)

    print(f'Checking if channel `{channel}` is active...')

    status = api.helix('streams', user_login=channel)['data']

    if not status:
        print('ERR: Channel is offline')
        sys.exit(1)
    else:
        status = status[0]

    print('Attempting to find ID of the live VOD...')

    vods = api.helix('videos', user_id=status['user_id'],
                     first=1, type='archive')['data']

    if len(vods) == 0:
        print('ERR: No VODs found on channel')
        sys.exit(1)

    stream_date = dateparser.isoparse(status['started_at'])
    vod_date = dateparser.isoparse(vods[0]['created_at'])

    if vod_date < stream_date:
        print('ERR: Live VOD is not available yet')
        sys.exit(1)

    return vods[0]["id"]


def main(argv=None):
    args = docopt(__doc__, argv=argv)

    global DEBUG
    DEBUG = args['--debug']

    output = args['-o']

    if output == '-':
        print('ERR: This script does not support stdout as output.')
        sys.exit(1)

    channel = args['<channel>']

    if args['--oauth'] and not args['<vod>']:
        v = find_vod(args['--oauth'], channel)
    else:
        print('Assuming that stream is online and VOD is correct')
        v = args['<vod>']

    stream = Stream(f'https://twitch.tv/{channel}',
                    oauth=args['--oauth'],
                    quality=args['--quality'],
                    threads=args['-j'])

    vod = Stream(f'https://twitch.tv/videos/{v}',
                 oauth=args['--oauth'],
                 quality=stream.quality,
                 threads=1)

    parts = 0

    for i in itertools.count():
        if os.path.exists(generate_filename(v, i)):
            if i == 0:
                print('Found previous segments, resuming download')

            parts = i + 1
        else:
            break

    parts = record(v, stream, vod, parts)

    try:
        t = create_timeline(v, parts)
    except TimelineMissingRangeError as ex:
        print(ex)
        print('ERR: Unable to concatenate segments!')
        sys.exit(1)

    if not output:
        output = f'{v}.ts'

    print(f'Writing stream recording to {output}')
    t.render(output, force=args['--force'])

    print('Cleaning up...')
    [os.unlink(generate_filename(v, part)) for part in range(parts)]


if __name__ == '__main__':
    main()
