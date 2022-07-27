from typing import Any, Dict
from requests import Session
from datetime import datetime
from hashlib import sha1
import dateutil.parser as dp


# Source: https://raw.githubusercontent.com/TwitchRecover/TwitchRecover/main/domains.txt
# TODO: Fetch updated list if possible
# TODO: Guess by VODs available on the channel
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
    @staticmethod
    def _session(token: str) -> Session:
        s = Session()
        s.headers['Client-ID'] = 'kimne78kx3ncx6brgo4mv6wki5h1ko'
        s.headers['Authorization'] = f'OAuth {token}'
        return s

    def __init__(self, oauth: str):
        self.token = oauth
        self.session = self._session(oauth)

    def gql(self, query: str) -> dict:
        res = self.session.post('https://gql.twitch.tv/gql', json={'query': query})

        if res.status_code == 200:
            return res.json()
        else:
            raise Exception(res.text)

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

    def vod_probe(self, stream: Dict[str, Any]) -> str:
        """Returns URL of VOD's playlist."""
        stream_id = stream['id']
        login = stream['broadcaster']['login']
        started_at = dp.parse(stream['createdAt'])

        path = vod_path(login, stream_id, started_at)

        for domain in VOD_DOMAINS:
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
