"""Usage:
    twitch_utils vod [options] [--header=<arg>]... (-c <channel> | -v <vod>) [-u <url>]

Parameters:
  output        Name of the output file. For more information see
                `twitch_utils concat --help`. Defaults to `<vod>.ts`.

Options:
  -u <url>          Force HLS playlist URL (bypass quality selection).
  -q <value>        Choose stream quality to save. To check available options
                    use -Q. Chunked == source. [default: chunked]
  -Q                Displays resolutions available in the HLS playlist, then
                    exits. Works only if the VOD is listed.
  -o <output>       Name of the output file. For more information see
                    `twitch_utils concat --help`. Defaults to `<vod>.ts`.
  -y, --force       Overwrite output file without confirmation.
  --no-concat       Only download all parts of the stream and ensure that
                    concatenation is possible.
  --header <value>  Add custom headers to all Twitch API calls (including
                    streamlink). Example: "X-Device-Id=value".
"""

import os
import re
import sys
from itertools import count
from docopt import docopt
from typing import Dict

from .concat import MissingRangesError
from .record import Stream, RecordingSession, create_timeline, generate_filename
from .twitch import TwitchAPI, VodNotFoundException
from .hls import SimpleHLS


def parse_usher(res: str) -> Dict[str, str]:
    streams = dict()
    quality = None
    source = False

    p = re.compile('.*(VIDEO|STABLE-VARIANT-ID)="(.*?)".*')

    for line in res.split('\n'):
        if line.startswith('#EXT-X-STREAM-INF:'):
            if m := p.match(line):
                quality = m.group(2)
            
            if 'IVS-VARIANT-SOURCE="source"' in line:
                source = True

        if not line.startswith('#') and quality:
            streams[quality] = line

            if source:
                streams['chunked'] = line

            quality = None
            source = False

    if 'chunked' not in streams:
        first_key = list(streams.keys())[0]
        streams['chunked'] = streams[first_key].replace(first_key, 'chunked')

    return streams


def resolve_playlist(args, api: TwitchAPI):
    url = args['-u']
    main_url = None

    if (vod := args['<vod>']):
        if url:
            return vod, url

        vod_obj = Stream(f'https://twitch.tv/videos/{vod}', None, api=api)
        main_url = vod_obj.stream_url()

    if (channel := args['<channel>']):
        stream = api.get_stream(channel)

        if not stream:
            print(f'ERR: Stream offline')
            sys.exit(1)

        try:
            vod = api.get_active_vod(stream)

            if url:
                return vod, url

            vod_obj = Stream(f'https://twitch.tv/videos/{vod}', None, api=api)
            main_url = vod_obj.stream_url()
        except VodNotFoundException:
            print('VOD is not listed, attempting to find the playlist')
            url = api.vod_probe(stream, args['-q'])
            vod = str(stream['id'])
            print(f'VOD found! Using stream ID {vod} as base name')
            return vod, url

    if main_url:
        res = api.session.get(main_url)

        if res.status_code != 200:
            print('ERR: Unable to read main playlist')
            sys.exit(1)

        streams = parse_usher(res.content.decode())

        if args['-Q']:
            print('Available streams: ' + ', '.join(streams.keys()))
            sys.exit(0)

        if args['-q'] not in streams:
            print(f'ERR: Quality {args["-q"]} is not available')
            sys.exit(1)

        url = streams[args['-q']]
    elif args['-Q']:
        print('ERR: Unable to find master playlist, -Q is not available')
        sys.exit(1)

    if not url:
        print(f'ERR: Unable to resolve URL of HLS playlist')

    return vod, url


def main(argv=None):
    args = docopt(__doc__, argv=argv)

    headers = {}

    for header in args['--header']:
        key, value = header.split('=', 1)
        headers[key] = value

    api = TwitchAPI(headers)
    vod, url = resolve_playlist(args, api)
    session = RecordingSession(vod, api)
    hls = SimpleHLS(url)

    for i in count():
        filename = generate_filename(vod, i)
        if not os.path.exists(filename):
            session.counter.set(i)
            break
    
    if session.counter.value > 0:
        print('Found previous segments, resuming download')

    result = False

    while not result:
        if session.counter.value > 0:
            try:
                timeline = create_timeline(vod, session.counter.value)
                missing_ranges = [(timeline.end, None)]
                offset = timeline.start
            except MissingRangesError as ex:
                missing_ranges = ex.ranges
                offset = ex.start
        else:
            missing_ranges = [(0, None)]
            offset = 0

        for start, end in missing_ranges:
            filename = session.next_file()
            print(f'Downloading {start}~{end} into {filename}')

            start = max(0, start - 30 - offset)
            end = (end + 30 - offset) if end else None

            with open(filename, 'wb') as fo:
                result = hls.download(fo, start=start, end=end)

                if end:  # not the last segment, continue
                    result = False

    output = args['-o']
    parts = session.counter.value

    if not output:
        output = f'{vod}.ts'

    try:
        t = create_timeline(vod, parts)
    except MissingRangesError as ex:
        print(ex)
        print('ERR: Unable to concatenate segments!')
        sys.exit(1)

    if parts == 1 and output.endswith('.ts') and not args['--no-concat']:
        print('Skipping concatenation (not required)')
        os.rename(generate_filename(vod, 0), output)
    elif not args['--no-concat']:
        print(f'Writing stream recording to {output}')
        t.render(output, force=args['--force'])

        print('Cleaning up...')
        [os.unlink(generate_filename(vod, part)) for part in range(parts)]
    else:
        files = ' '.join(generate_filename(vod, part) for part in range(parts))

        print('Use this command to concatenate parts into a full video:')
        print(f'> twitch_utils concat {files} -o {output}')


if __name__ == '__main__':
    main()