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
from itertools import count
from datetime import datetime
from dataclasses import dataclass, field
from typing import Callable, Dict, Union

from twitch_utils.hls import SimpleHLS

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
from subprocess import Popen, PIPE, DEVNULL

from .clip import Clip
from .concat import Timeline, MissingRangesError
from .twitch import TwitchAPI, VodException, VodNotFoundException


DEBUG = False


class Stream(object):
    PARSE_QUEUED = compile('{} Adding segment {segment:d} to queue{}')
    PARSE_COMPLETE = compile('{} Segment {segment:d} complete{}')
    PARSE_FAILED = compile('{} Download of segment {segment:d} failed{}')
    PARSE_DISCARDED = compile('{} Discarding segment {segment:d}{}')

    def __init__(self, url: str,
                 quality: Union[str, None] = 'best',
                 threads: int = 1,
                 api: Union[TwitchAPI, None] = None,
                 live: bool = False):
        self.url = url
        self.quality = quality
        self.threads = threads
        self.api = api
        self.live = live

        self.result = -1
        self.started = Event()
        self._stream_url = None

    def copy(self):
        return Stream(self.url, self.quality, self.threads,
                      self.api)

    def _args(self) -> list:
        params: Dict[str, Union[str, int]] = {
            'stream-segment-attempts': 5,
            'stream-segment-threads': self.threads
        }

        args = ['-l', 'debug', '--no-config', '--twitch-disable-ads',
                '--twitch-low-latency']
        args += [f'--{key}={value}' for key, value in params.items()]

        if self.api:
            for key, value in self.api.get_headers().items():
                args += [f'--twitch-api-header={key}={value}']

        args.append(self.url)

        if self.quality:
            args.append(self.quality)

        args.append('-O')

        return args

    def download(self, dest: str) -> int:
        """Exit codes: 0 - success, 1 - should retry, 2 - should stop"""

        print(f'Downloading `{self.url}` into {dest}')
        exit_code = 0

        fo = open(dest, 'wb')

        sl_cmd = ['streamlink'] + self._args()
        sl_env = {**os.environ, 'PYTHONUNBUFFERED': '1'}
        sl_kwargs = {'stdout': PIPE,
                     'stderr': PIPE,
                     'text': True,
                     'env': sl_env}
        sl_proc = Popen(sl_cmd, **sl_kwargs)

        ff_cmd = ['ffmpeg', '-hide_banner',
                  '-i', '-',
                  '-c', 'copy', '-copyts',
                  '-f', 'mpegts', '-']
        ff_kwargs = {'stdin': sl_proc.stdout,
                     'stdout': fo,
                     'stderr': DEVNULL}
        ff_proc = Popen(ff_cmd, **ff_kwargs)

        sl_proc.stdout.close()

        expected, downloaded = [-1] * 2
        first_segment = True
        ts = 0

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

            if fo.tell() > 0 and ts > 0 and not self.started.is_set():
                # Log precise timings to leave some traces for manual
                # checks of recording's consistency
                # For example: If the following calculation
                #       (ts2 - ts1) - (inpoint2 - inpoint1)
                # is > 0, we can assume that Twitch has lost some segments
                # and the stream has become shorter by this amount.
                inpoint = Clip(dest).inpoint
                print(f'Clip {dest} started at {ts} with offset {inpoint}')
                self.started.set()

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
                    # First write is delayed, log timestamp now
                    ts = datetime.now().timestamp()
                    first_segment = False

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
        ff_proc.wait()

        fo.flush()
        fo.close()

        if downloaded < expected:
            print(f'ERR: Skipped segment {expected}')
            exit_code = 1

        return exit_code

    def _target_download(self, *args):
        self.result = None
        self.result = self.download(*args)

        # Unlock waiting threads if exit was early
        self.started.set()
        self.started.clear()

    def download_async(self, dest: str) -> Thread:
        self.started.clear()
        p = Thread(target=self._target_download, args=(dest,))
        p.start()
        return p

    def stream_url(self) -> str:
        if not self.live and self._stream_url:
            return self._stream_url

        sl_cmd = ['streamlink'] + self._args() + ['--stream-url']
        sl_env = {**os.environ, 'PYTHONUNBUFFERED': '1'}
        sl_kwargs = {'stdout': None,
                     'stderr': PIPE,
                     'text': True,
                     'env': sl_env}
        sl_proc = Popen(sl_cmd, **sl_kwargs)
        sl_proc.wait()

        url = sl_proc.stderr.readline().strip()
        self._stream_url = url
        return url

    def clip(self) -> Clip:
        return Clip(self.stream_url())


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
                 vod_url: Union[str, None] = None,
                 quality: str = 'best'):
        super().__init__()

        self.session = session

        if not vod_url:
            vod_url = f'https://twitch.tv/videos/{session.vod}'

        stream = Stream(vod_url, api=session.api, quality=quality)
        self.hls = SimpleHLS(stream.stream_url())

    def first_vod(self):
        try:
            timeline = create_timeline(self.session.vod, self.session.counter.value)
            start = timeline.start
        except MissingRangesError as ex:
            start = ex.start

        result = None
        filename = self.session.next_file()

        while not result:
            if result is not None:
                print('WARN: Could not download VOD (exit code '
                        f'{result}), retrying in 60 seconds...')
                sleep(60)

            with open(filename, 'wb') as fo:
                self.hls.download(fo, end = start + 30)

            try:
                Clip(filename)
                result = True
            except Exception:
                result = False

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
                    create_timeline(self.session.vod, self.session.counter.value)
                    print('Timeline is complete, good!')
                    missing_parts = None
                    break
                except MissingRangesError as ex:
                    offset = ex.start
                    missing_parts = self.optimize_missing(ex.ranges)
                    print(f'WARN: {ex}')

                for (start, end) in missing_parts:
                    print(f'Downloading segment {start}~{end}')

                    filename = self.session.next_file()

                    with open(filename, 'wb') as fo:
                        self.hls.download(fo,
                                          start = max(0, start - 30 - offset),
                                          end = end + 30 - offset)

            if self.session.recording.is_set():
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
    except VodException:
        vod = stream['id']
        print(f'WARN: VOD is not listed, using stream ID {vod} as base name')

    session = RecordingSession(vod, api)

    for i in count():
        filename = generate_filename(vod, i)
        if not os.path.exists(filename):
            session.counter.set(i)
            break

    if (resumed := session.counter.value > 0):
        print('Found previous segments, resuming download')

    def is_online():
        if api and stream:
            try:
                return api.is_still_live(stream)
            except Exception:
                return True
        else:
            return True

    print('Starting record thread')
    record = RecordThread(session, channel, args['--quality'], args['-j'], is_online)
    record.start()

    if vod == stream['id']:
        if not resumed:
            session.recording.wait()

        print('Attempting to find the VOD playlist')

        clip = None

        for i in range(session.counter.value):
            try:
                clip = Clip(generate_filename(vod, i))
            except Exception:
                pass

        if not clip:
            print('ERR: No readable segments found, unable to get resolution')
            sys.exit(1)

        height = clip.height
        qualities = []
        if height >= 720:
            qualities += [f'{height}p60', f'{height}p30']
        else:
            qualities += [f'{height}']
        qualities += ['chunked']

        for i in qualities:
            try:
                vod_url = api.vod_probe(stream, i)
            except VodNotFoundException:
                pass

        if not vod_url:
            print(f'ERR: VOD not found')
            sys.exit(1)

    print('Starting repair thread')
    repair = RepairThread(session, vod_url, args['--quality'])
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
