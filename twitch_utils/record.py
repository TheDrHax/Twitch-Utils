"""Usage:
    twitch_utils record [options] [--header=<arg>]... <channel> [<vod>]

Parameters:
  channel   Name of the channel. Can be found in the URL: twitch.tv/<channel>
  vod       VOD of stream that is currently live. Can be found in the URL:
            twitch.tv/videos/<vod>

Options:
  --quality <value> Choose stream quality to save. Accepts the same values as
                    streamlink. To check available options use command
                    `streamlink twitch.tv/<channel>`. [default: best]
  --oauth <token>   Twitch OAuth token. You need to extract it from the site's
                    cookie named "auth-token". Equivalent to
                    header="Authorization=OAuth <token>".
  --header <value>  Add custom headers to all Twitch API calls (including
                    streamlink). Example: "X-Device-Id=value".
  -o <name>         Name of the output file. For more information see
                    `twitch_utils concat --help`. Defaults to `<vod>.ts`.
  -j <threads>      Number of concurrent downloads of live segments. [default: 4]
  -y, --force       Overwrite output file without confirmation.
  --no-concat       Only download all parts of the stream and ensure that
                    concatenation is possible.
  --debug           Forward output of streamlink and ffmpeg to stderr.
"""

import os
import sys
import math
import itertools
from datetime import datetime
from typing import Dict, Union, Any

try:
    import streamlink
    from parse import compile
except ImportError:
    print('Error: You need to install tdh-twitch-utils[record] or '
          'tdh-twitch-utils[all] to use this feature.',
          file=sys.stderr)
    sys.exit(1)

from time import sleep
from docopt import docopt
from subprocess import Popen, PIPE
from multiprocessing import Process

from .clip import Clip
from .concat import Timeline, TimelineMissingRangeError
from .twitch import TwitchAPI


DEBUG = False


class Stream(object):
    PARSE_QUEUED = compile('{} Adding segment {segment:d} to queue{}')
    PARSE_COMPLETE = compile('{} Segment {segment:d} complete{}')

    def __init__(self, url: str,
                 quality: str = 'best',
                 threads: int = 1,
                 api: Union[TwitchAPI, None] = None,
                 start: int = 0,
                 end: Union[int, None] = None,
                 live: bool = False):
        self.url = url
        self.quality = quality
        self.threads = threads
        self.api = api
        self.start = start
        self.end = end
        self.live = live

    def copy(self):
        return Stream(self.url, self.quality, self.threads,
                      self.api, self.start, self.end)

    def _args(self) -> list:
        params: Dict[str, Union[str, int]] = {
            'stream-segment-attempts': 5,
            'stream-segment-threads': self.threads
        }

        if self.start > 0:
            params['hls-start-offset'] = math.floor(self.start)

        if self.end:
            params['hls-duration'] = math.ceil(self.end - self.start)

        args = ['-l', 'debug', '--no-config', '--twitch-disable-ads',
                '--twitch-low-latency']
        args += [f'--{key}={value}' for key, value in params.items()]

        if self.api:
            for key, value in self.api.get_headers().items():
                args += [f'--twitch-api-header={key}={value}']

        args += [self.url, self.quality, '-O']

        return args

    def download(self, dest: str) -> int:
        """Exit codes: 0 - success, 1 - should retry, 2 - should stop"""

        print(f'Downloading `{self.url}` into {dest}')
        exit_code = 0

        fo = open(dest, 'wb')
        sl_cmd = ['streamlink'] + self._args()
        sl_env = {**os.environ, 'PYTHONUNBUFFERED': '1'}
        sl_kwargs = {'stdout': fo,
                     'stderr': PIPE,
                     'text': True,
                     'env': sl_env}
        sl_proc = Popen(sl_cmd, **sl_kwargs)

        expected, downloaded = [-1] * 2
        first_segment = True

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

                if self.live and first_segment:
                    # Log precise timings to leave some traces for manual
                    # checks of recording's consistency
                    # For example: If the following calculation
                    #       (ts2 - ts1) - (inpoint2 - inpoint1)
                    # is > 0, we can assume that Twitch has lost some segments
                    # and the stream has become shorter by this amount.
                    ts = datetime.now().timestamp()
                    inpoint = Clip(dest).inpoint
                    print(f'Clip {dest} started at {ts} with offset {inpoint}')
                    first_segment = False

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

        if downloaded < expected:
            print(f'ERR: Skipped segment {expected}')
            exit_code = 1

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


def record(channel_name: str, vod_id: str, vod_url: Union[str, None] = None,
           quality: str = 'best', threads: int = 4,
           parts: int = 0,
           api: Union[TwitchAPI, None] = None,
           stream_obj: Union[Dict[str, Any], None] = None) -> int:
    stream_result = -1
    missing_part = None

    stream = Stream(f'https://twitch.tv/{channel_name}',
                    api=api,
                    quality=quality,
                    threads=threads,
                    live=True)

    if not vod_url:
        vod_url = f'https://twitch.tv/videos/{vod_id}'

    vod = Stream(vod_url,
                 api=api,
                 quality=quality,
                 threads=1)

    while stream_result != 0:
        resumed = parts > 0

        if resumed:
            print('Resuming download of live stream')
        else:
            print('Starting download of live stream')

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

            vod_result = -1
            filename = generate_filename(vod_id, parts)

            while vod_result != 0:
                if vod_result > 0:
                    print('WARN: Could not download VOD (exit code '
                          f'{vod_result}), retrying in 60 seconds...')
                    sleep(60)

                vod_result = vod.download(filename)

                if vod_result == 0:
                    try:
                        Clip(filename)
                    except Exception:
                        vod_result = 4095

            parts += 1
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
                      'retrying in 120 seconds...')
                sleep(120)

            print(f'Downloading segment {missing_part[0]}~{missing_part[1]}')

            segment = vod.copy()
            segment.start = max(0, missing_part[0] - 60)
            segment.end = missing_part[1] + 60

            vod_proc = segment.download_async(generate_filename(vod_id, parts))
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

            if api and stream_obj and not api.is_still_live(stream_obj):
                print('Stream ended')
                break

    print('All parts are downloaded!')
    return parts


def main(argv=None):
    args = docopt(__doc__, argv=argv)

    global DEBUG
    DEBUG = args['--debug']

    output = args['-o']

    if output == '-':
        print('ERR: This script does not support stdout as output.')
        sys.exit(1)

    channel = args['<channel>']

    api = None
    stream = None
    vod = args['<vod>']
    vod_url = None

    headers = {}

    for header in args['--header']:
        key, value = header.split('=', 1)
        headers[key] = value

    if args['--oauth']:
        headers['Authorization'] = 'OAuth ' + args['--oauth']

    api = TwitchAPI(headers)
    stream = api.get_stream(channel)

    try:
        if not vod:
            vod = api.get_active_vod(stream)
            vod_url = None
    except Exception:
        print('VOD is not listed, attempting to find the playlist')
        vod_url = api.vod_probe(stream)
        vod = stream['id']
        print(f'VOD found! Using stream ID {vod} as base name')

        if args['--quality'] != 'best':
            print('WARN: Resetting quality to `best` (other options '
                    'are not supported yet)')
            args['--quality'] = 'best'

    if not vod:
        print('ERR: Unable to find VOD')
        sys.exit(1)

    parts = 0

    for i in itertools.count():
        if os.path.exists(generate_filename(vod, i)):
            if i == 0:
                print('Found previous segments, resuming download')

            parts = i + 1
        else:
            break

    parts = record(channel, vod, vod_url, args['--quality'], args['-j'], parts, api, stream)

    try:
        t = create_timeline(vod, parts)
    except TimelineMissingRangeError as ex:
        print(ex)
        print('ERR: Unable to concatenate segments!')
        sys.exit(1)

    if not args['--no-concat']:
        if not output:
            output = f'{vod}.ts'

        print(f'Writing stream recording to {output}')
        t.render(output, force=args['--force'])

        print('Cleaning up...')
        [os.unlink(generate_filename(vod, part)) for part in range(parts)]
    else:
        if not output:
            output = f'{vod}.mp4'

        files = ' '.join(generate_filename(vod, part) for part in range(parts))

        print('Use this command to concatenate parts into a full video:')
        print(f'> twitch_utils concat {files} -o {output}')


if __name__ == '__main__':
    main()
