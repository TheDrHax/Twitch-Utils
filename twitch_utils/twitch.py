from typing import Any, Dict, Union, List
from requests import Session
from datetime import datetime
from hashlib import sha1
from urllib.parse import urlparse
import dateutil.parser as dp
from streamlink import NoPluginError

try:
    from streamlink import Streamlink
except ImportError:
    Streamlink = None


# Source: https://raw.githubusercontent.com/TwitchRecover/TwitchRecover/main/domains.txt
# TODO: Fetch updated list if possible
VOD_DOMAINS = [
    'vod-secure.twitch.tv',
    'vod-metro.twitch.tv',
    'vod-pop-secure.twitch.tv',
    'd2e2de1etea730.cloudfront.net',
    'dqrpb9wgowsf5.cloudfront.net',
    'ds0h3roq6wcgc.cloudfront.net',
    'd2nvs31859zcd8.cloudfront.net',
    'd2aba1wr3818hz.cloudfront.net',
    'd3c27h4odz752x.cloudfront.net',
    'dgeft87wbj63p.cloudfront.net',
    'd1m7jfoe9zdc1j.cloudfront.net'
]


# Source: https://github.com/TwitchRecover/TwitchRecover/blob/
# 48b32dccec752961b6402fff50eefcdc97ca27ff
# /src/TwitchRecover.Core/Compute.java#L51
def vod_path(channel: str, stream_id: str, started_at: datetime) -> str:
    base = f'{channel.lower()}_{stream_id}_{int(started_at.timestamp())}'
    hash = sha1(base.encode()).hexdigest()[:20]
    return f'/{hash}_{base}/chunked/index-dvr.m3u8'


class TwitchAPI:
    def __init__(self, headers: Dict[str, str] = {}):
        self.session = Session()

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
            raise Exception(res.text)

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
            raise Exception('Channel not found')

        stream: Dict[str, Any] = user['stream']

        if stream is None:
            raise Exception('Stream appears to be offline')
        
        return stream

    def get_active_vod(self, stream: Dict[str, Any], stream_type: str = 'live'):
        """Returns ID of VOD if stream is live."""

        if stream['type'] != stream_type:
            raise Exception(f'Stream type is "{stream["type"]}" '
                            f'instead of "{stream_type}"')

        vod: Dict[str, Any] = stream['archiveVideo']

        if vod is None:
            raise Exception('VOD not found')

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

    def vod_probe(self, stream: Dict[str, Any]) -> str:
        """Returns URL of VOD's playlist."""
        stream_id = stream['id']
        login = stream['broadcaster']['login']
        started_at = dp.parse(stream['createdAt'])

        path = vod_path(login, stream_id, started_at)

        # Try domain from previous VOD first
        predicted_domain = self.vod_probe_domain(login)
        domains = sorted(VOD_DOMAINS, key=lambda x: x != predicted_domain)

        for domain in domains:
            url = f'https://{domain}{path}'
            res = self.session.head(url)

            if res.status_code == 200:
                return url

        raise Exception('VOD not found')

    def is_still_live(self, stream: Dict[str, Any]) -> bool:
        try:
            channel = stream['broadcaster']['login']
            return self.get_stream(channel)['id'] == stream['id']
        except Exception:
            return False
