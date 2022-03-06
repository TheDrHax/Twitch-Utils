from typing import Any, Dict
from requests import Session


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

    def get_stream_id(self, login: str, stream_type: str = 'live'):
        """Returns ID of VOD if stream is live."""

        res = self.gql(f'''
            query {{
                user(login: "{login}") {{
                    stream {{
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

        if stream['type'] != stream_type:
            raise Exception(f'Stream type is "{stream["type"]}" '
                            f'instead of "{stream_type}"')

        vod: Dict[str, Any] = stream['archiveVideo']

        if vod is None:
            raise Exception('VOD not found')

        return vod['id']
