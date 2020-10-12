from requests import Session


class TwitchAPI:
    @staticmethod
    def _session(token: str) -> Session:
        s = Session()
        s.headers['Acccept'] = 'application/vnd.twitchtv.v5+json'
        s.headers['Client-ID'] = 'kimne78kx3ncx6brgo4mv6wki5h1ko'
        s.headers['Authorization'] = f'Bearer {token}'
        return s

    def __init__(self, oauth: str):
        self.session = self._session(oauth)

    def get(self, namespace: str, method: str, **payload):
        url = f'https://api.twitch.tv/{namespace}/{method}'

        if len(payload.keys()) > 0:
            params = '&'.join([f'{k}={v}' for k, v in payload.items()])
            url += '?' + params

        res = self.session.get(url)

        if res.status_code == 200:
            return res.json()
        else:
            raise Exception(res.text)

    def helix(self, method: str, **payload):
        return self.get('helix', method, **payload)
