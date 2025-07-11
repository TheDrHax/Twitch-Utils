from typing import Any, Dict, Union, List
from retry_requests import retry
from datetime import datetime
from hashlib import sha1
from urllib.parse import urlparse
import dateutil.parser as dp

try:
    from streamlink import Streamlink
    from streamlink.exceptions import NoPluginError
except ImportError:
    Streamlink = None

    class NoPluginError(Exception):
        pass


# Source: https://raw.githubusercontent.com/TwitchRecover/TwitchRecover/main/domains.txt
# TODO: Fetch updated list if possible
VOD_DOMAINS = [
    'vod-metro.twitch.tv',
    'vod-pop-secure.twitch.tv',
    'vod-secure.twitch.tv',
    # 'd1g1f25tn8m2e6.cloudfront.net',
    # 'd1m7jfoe9zdc1j.cloudfront.net',
    # 'd1mhjrowxxagfy.cloudfront.net',
    # 'd1oca24q5dwo6d.cloudfront.net',
    # 'd1w2poirtb3as9.cloudfront.net',
    # 'd1xhnb4ptk05mw.cloudfront.net',
    # 'd1ymi26ma8va5x.cloudfront.net',
    # 'd2aba1wr3818hz.cloudfront.net',
    # 'd2dylwb3shzel1.cloudfront.net',
    # 'd2e2de1etea730.cloudfront.net',
    # 'd2nvs31859zcd8.cloudfront.net',
    # 'd2um2qdswy1tb0.cloudfront.net',
    # 'd2vjef5jvl6bfs.cloudfront.net',
    # 'd2xmjdvx03ij56.cloudfront.net',
    # 'd3fi1amfgojobc.cloudfront.net',
    # 'd36nr0u3xmc4mm.cloudfront.net',
    # 'd3aqoihi2n8ty8.cloudfront.net',
    # 'd3c27h4odz752x.cloudfront.net',
    # 'd3vd9lfkzbru3h.cloudfront.net',
    # 'd6d4ismr40iw.cloudfront.net',
    # 'd6tizftlrpuof.cloudfront.net',
    # 'ddacn6pr5v0tl.cloudfront.net',
    # 'dgeft87wbj63p.cloudfront.net',
    # 'dqrpb9wgowsf5.cloudfront.net',
    # 'ds0h3roq6wcgc.cloudfront.net',
    # 'dykkng5hnh52u.cloudfront.net',
]


# Source: https://github.com/TwitchRecover/TwitchRecover/blob/
# 48b32dccec752961b6402fff50eefcdc97ca27ff
# Source: https://github.com/tanersb/TwitchRecover/blob/
# bee8cc29fd44b00070c96c4c4c0d1b6ad811dcbd/recover.py#L14-L42
# /src/TwitchRecover.Core/Compute.java#L51
def vod_path(channel: str, stream_id: str,
             started_at: datetime, quality: str) -> str:
    base = f'{channel.lower()}_{stream_id}_{int(started_at.timestamp())}'
    hash = sha1(base.encode()).hexdigest()[:20]
    return f'/{hash}_{base}/{quality}/index-dvr.m3u8'


class TwitchException(Exception):
    pass


class ChannelNotFoundException(TwitchException):
    def __init__(self):
        super().__init__('Channel not found')


class StreamOfflineException(TwitchException):
    def __init__(self):
        super().__init__('Stream appears to be offline')


class VodException(TwitchException):
    pass


class VodNotFoundException(VodException):
    def __init__(self):
        super().__init__('VOD not found')


class GqlException(TwitchException):
    def __init__(self, text):
        super().__init__(text)


class VodTypeMismatchException(VodException):
    def __init__(self, expected, actual):
        super().__init__(f'Stream type is "{actual}" instead of "{expected}"')


class TwitchAPI:
    def __init__(self, headers: Dict[str, str] = {}):
        self.session = retry()

        if Streamlink:
            self.sl = Streamlink()
            self.session.headers = self.sl.http.headers
        else:
            self.sl = None

        self.session.headers['Client-ID'] = 'ue6666qo983tsx6so1t0vnawi233wa'
        self.session.headers.update(headers)

    def gql(self, query: str) -> dict:
        res = self.session.post('https://gql.twitch.tv/gql', json={'query': query})

        if res.status_code == 200:
            return res.json()
        else:
            raise GqlException(res.text)

    def get_headers(self) -> Dict[str, Union[str, bytes]]:
        return dict(self.session.headers)

    def get_stream(self, login: str) -> Dict[str, Any]:
        res = self.gql(f'''
            query {{
                user(login: "{login}") {{
                    stream {{
                        id
                        broadcaster {{
                            login
                        }}
                        createdAt
                        archiveVideo {{
                            id
                        }}
                        type
                    }}
                }}
            }}
        ''')

        user: Dict[str, Any] = res['data']['user']

        if user is None:
            raise ChannelNotFoundException

        stream: Dict[str, Any] = user['stream']

        if stream is None:
            raise StreamOfflineException
        
        return stream

    def get_active_vod(self, stream: Dict[str, Any], stream_type: str = 'live'):
        """Returns ID of VOD if stream is live."""

        if stream['type'] != stream_type:
            raise VodTypeMismatchException(stream_type, stream['type'])

        vod: Dict[str, Any] = stream['archiveVideo']

        if not vod:
            raise VodNotFoundException

        return vod['id']

    def get_vod_ids(self, login: str, first: int = 10) -> List[str]:
        res = self.gql(f'''
            query {{
                user(login: "{login}") {{
                    videos(first: {first}) {{
                        edges {{
                            node {{
                                id
                            }}
                        }}
                    }}
                }}
            }}
        ''')

        videos = res['data']['user']['videos']['edges']
        return [video['node']['id'] for video in videos]

    def vod_probe_domain(self, login: str) -> Union[str, None]:
        if not self.sl:
            return None

        try:
            prev_vod = self.get_vod_ids(login, first=1)[0]
        except IndexError:
            return None

        try:
            streams = self.sl.streams(f'twitch.tv/videos/{prev_vod}')
        except NoPluginError:
            return None

        stream = list(streams.values())[0]
        url = urlparse(stream.url)
        return url.hostname

    def vod_probe(self, stream: Dict[str, Any], quality: str = 'chunked') -> str:
        """Returns URL of VOD's playlist."""
        stream_id = stream['id']
        login = stream['broadcaster']['login']
        started_at = dp.parse(stream['createdAt'])

        # Try domain from previous VOD first
        predicted_domain = self.vod_probe_domain(login)

        if predicted_domain and predicted_domain not in VOD_DOMAINS:
            VOD_DOMAINS.append(predicted_domain)

        domains = sorted(VOD_DOMAINS, key=lambda x: x != predicted_domain)

        for domain in domains:
            path = vod_path(login, stream_id, started_at, quality)
            url = f'https://{domain}{path}'
            res = self.session.head(url, timeout=5)
            print(f'[{res.status_code}] {url}')

            if res.status_code == 200:
                return url

        raise VodNotFoundException

    def is_still_live(self, stream: Dict[str, Any]) -> bool:
        try:
            channel = stream['broadcaster']['login']
            return self.get_stream(channel)['id'] == stream['id']
        except StreamOfflineException:
            return False
