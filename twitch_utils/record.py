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
from itertools import count
from datetime import datetime
from dataclasses import dataclass, field
from typing import Callable, Dict, Union

try:
    import streamlink
    from parse import compile
except ImportError:
    print('Error: You need to install tdh-twitch-utils[record] or '
          'tdh-twitch-utils[all] to use this feature.',
          file=sys.stderr)
    sys.exit(1)

from time import sleep
from threading import Lock, Thread, Event
from docopt import docopt
from subprocess import Popen, PIPE

from .clip import Clip
from .concat import Timeline, MissingRangesError
from .twitch import TwitchAPI, VodException


DEBUG = False


class Stream(object):
    PARSE_QUEUED = compile('{} Adding segment {segment:d} to queue{}')
    PARSE_COMPLETE = compile('{} Segment {segment:d} complete{}')
    PARSE_FAILED = compile('{} Download of segment {segment:d} failed{}')
    PARSE_DISCARDED = compile('{} Discarding segment {segment:d}{}')

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

        self.result = -1
        self.started = Event()

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
            failed = Stream.PARSE_FAILED.parse(line)
            discarded = Stream.PARSE_DISCARDED.parse(line)

            if failed or (not first_segment and discarded):
                print(f'ERR: {line}')
                sl_proc.terminate()
                exit_code = 1
                break
            elif queued:
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

                self.started.set()

                if downloaded == -1 or downloaded + 1 == segment:
                    downloaded = segment
                else:
                    print(f'ERR: Skipped segment {downloaded + 1}')
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
        self.started.clear()

        self.result = None
        self.result = self.download(*args)

        # Unlock waiting threads if exit was early
        self.started.set()

    def download_async(self, dest: str) -> Thread:
        p = Thread(target=self._target_download, args=(dest,))
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


class Counter:
    def __init__(self, value = 0):
        self._value = value
        self.lock = Lock()

    def set(self, value):
        with self.lock:
            self._value = value
            return self._value

    def inc(self):
        with self.lock:
            self._value += 1
            return self._value
    
    def dec(self):
        with self.lock:
            self._value -= 1
            return self._value

    @property
    def value(self):
        return self._value


@dataclass
class RecordingSession:
    vod: str
    api: Union[TwitchAPI, None] = None
    counter: Counter = field(default_factory=Counter)
    recording: Event = field(default_factory=Event)
    dirty: Event = field(default_factory=Event)

    def next_file(self) -> str:
        return generate_filename(self.vod, self.counter.inc() - 1)


class RecordThread(Thread):
    def __init__(self, session: RecordingSession,
                 channel_name: str,
                 quality: str = 'best', threads: int = 4,
                 is_online: Callable = lambda: True):
        super().__init__()

        self.session = session
        self.stream = Stream(f'https://twitch.tv/{channel_name}',
                             api=session.api,
                             quality=quality,
                             threads=threads,
                             live=True)
        self.is_online = is_online

    def run(self):
        result = -1

        while result != 0:
            filename = self.session.next_file()
            proc = self.stream.download_async(filename)

            # Wait for possible early exit
            self.stream.started.wait()

            if self.stream.result == 2:
                if self.session.counter.value == 1:
                    sys.exit(1)
                else:
                    break

            self.session.recording.set()
            self.session.dirty.set()

            proc.join()
            result = self.stream.result

            print(f'Finished download of live stream (exit code: {result})')

            if result != 0:
                print('Resuming in 60 seconds...')
                sleep(60)

                if not self.is_online():
                    print('Stream ended')
                    break

        self.session.recording.clear()
        self.session.dirty.set()


class RepairThread(Thread):
    def __init__(self, session: RecordingSession,
                 vod_id: str, vod_url: Union[str, None] = None,
                 quality: str = 'best'):
        super().__init__()

        self.session = session

        if not vod_url:
            vod_url = f'https://twitch.tv/videos/{vod_id}'

        self.stream = Stream(vod_url,
                             api=session.api,
                             quality=quality,
                             threads=1)
        self.vod = vod_id

    def first_vod(self):
        result = -1
        filename = self.session.next_file()

        while result != 0:
            if result > 0:
                print('WARN: Could not download VOD (exit code '
                        f'{result}), retrying in 60 seconds...')
                sleep(60)

            result = self.stream.download(filename)

            if result == 0:
                try:
                    Clip(filename)
                except Exception:
                    result = 4095

        print(f'Finished download of VOD (exit code: {result})')

    @staticmethod
    def optimize_missing(ranges: list[tuple]) -> list[tuple]:
        ranges = list(ranges)
        ranges.sort(key=lambda x: x[0])

        optimized = []

        for i in range(len(ranges)):
            if i == 0:
                optimized.append(ranges[i])
                continue

            (a1, a2), (b1, b2) = optimized[-1], ranges[i]

            if (a2 - a1 + b2 - b1) > (b1 - a2):
                optimized[-1] = (a1, b2)
            else:
                optimized.append(ranges[i])

        return optimized

    def run(self):
        self.session.recording.wait()

        missing_parts = None

        # Retry first VOD at least until it is readable
        if self.session.counter.value == 1:
            print('Starting download of VOD')
            self.first_vod()

        while self.session.recording.is_set():
            while missing_parts or self.session.dirty.is_set():
                self.session.dirty.clear()
                print('Testing the possibility of concatenation')

                try:
                    create_timeline(self.vod, self.session.counter.value)
                    print('Timeline is complete, good!')
                    missing_parts = None
                    break
                except MissingRangesError as ex:
                    missing_parts = self.optimize_missing(ex.ranges)
                    print(f'WARN: {ex}')
                    print('Retrying in 120 seconds...')
                    if self.session.dirty.wait(120):
                        print('Wait interrupted, rechecking')
                        break

                for (start, end) in missing_parts:
                    print(f'Downloading segment {start}~{end}')
                    segment = self.stream.copy()
                    segment.start = max(0, start - 60)
                    segment.end = end + 60

                    filename = self.session.next_file()
                    segment.download(filename)

            self.session.dirty.wait()

        print('All parts are downloaded!')


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
    except VodException:
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

    session = RecordingSession(vod, api)

    for i in count():
        filename = generate_filename(vod, i)
        if not os.path.exists(filename):
            session.counter.set(i)
            break

    if session.counter.value > 0:
        print('Found previous segments, resuming download')

    def is_online():
        if api and stream:
            return api.is_still_live(stream)
        else:
            return True

    record = RecordThread(session, channel, args['--quality'], args['-j'], is_online)
    repair = RepairThread(session, vod, vod_url, args['--quality'])

    record.start()
    repair.start()

    record.join()
    repair.join()

    parts = session.counter.value

    try:
        t = create_timeline(vod, parts)
    except MissingRangesError as ex:
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
