from requests import Session


class TwitchAPI:
    @staticmethod
    def _session(token: str) -> Session:
        s = Session()
        s.headers['Client-ID'] = 'kimne78kx3ncx6brgo4mv6wki5h1ko'
        s.headers['Authorization'] = f'OAuth {token}'
        return s

    def __init__(self, oauth: str):
        self.session = self._session(oauth)

    def gql(self, query: str) -> dict:
        res = self.session.post('https://gql.twitch.tv/gql', json={'query': query})

        if res.status_code == 200:
            return res.json()
        else:
            raise Exception(res.text)

    def find_vod(self, user: str) -> str:
        res = self.gql(f'''
            query {{
                user(login: "{user}") {{
                    stream {{
                        archiveVideo {{
                            id
                        }}
                    }}
                }}
            }}
        ''')

        user = res['data']['user']

        if user is None:
            raise Exception('Channel not found')
        
        stream = user['stream']

        if stream is None:
            raise Exception('Stream appears to be offline')

        return stream['archiveVideo']['id']