"""Exercise the real frontend proxy and backend without external API/LLM calls."""
import argparse
from http.cookiejar import CookieJar
import json
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener


def smoke(base_url: str) -> None:
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    def request(path, data=None):
        payload = json.dumps(data).encode() if data is not None else None
        with client.open(Request(base_url + path, data=payload,
                                 headers={'Content-Type': 'application/json'}), timeout=5) as response:
            return json.load(response)

    if request('/api/health') != {'status': 'ok'}:
        raise RuntimeError('Frontend proxy health failed')
    try:
        request('/api/v1/ferry/stops')
    except HTTPError as error:
        if error.code != 401:
            raise
    else:
        raise RuntimeError('Protected endpoint allowed unauthenticated access')
    request('/api/auth/login', {'password': 'ci_smoke_password'})
    result = request('/api/v1/ferry/stops')
    if not isinstance(result.get('stops'), list) or not result['stops']:
        raise RuntimeError('Authenticated timetable response missing stops')
    print('PASS: frontend proxy, authentication and timetable integration')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://localhost:18080')
    smoke(parser.parse_args().base_url)
