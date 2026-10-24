"""Small HTTP boundary; error messages deliberately exclude response bodies."""
import gzip
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request


class RemoteError(RuntimeError):
    def __init__(self, host, status=None):
        self.status = status
        super().__init__(f'{host}: HTTP {status}' if status else f'{host}: network/response failure')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward Authorization or API keys through an unexpected redirect.
        return None


class HttpClient:
    def __init__(self, timeout=45, sleep=time.sleep):
        self.timeout = timeout
        self.sleep = sleep
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, url, *, body=None, headers=None, retry=None):
        headers = {'User-Agent': 'tw-stock-digest/0.1', 'Accept': 'application/json',
                   'Accept-Encoding': 'gzip', **(headers or {})}
        retry = method in ('GET', 'PATCH', 'PUT') if retry is None else retry
        attempts = 3 if retry else 1
        host = urllib.parse.urlsplit(url).hostname
        for attempt in range(attempts):
            try:
                req = urllib.request.Request(url, data=body, headers=headers, method=method)
                with self.opener.open(req, timeout=self.timeout) as response:
                    result = response.read()
                    if response.headers.get('Content-Encoding') == 'gzip':
                        result = gzip.decompress(result)
                    return result
            except urllib.error.HTTPError as exc:
                status = exc.code
                exc.close()
                if status not in (429, 500, 502, 503, 504, 529) or attempt == attempts - 1:
                    raise RemoteError(host, status) from None
            except (urllib.error.URLError, TimeoutError, OSError,
                    http.client.HTTPException, EOFError):
                if attempt == attempts - 1:
                    raise RemoteError(host) from None
            self.sleep(2 ** attempt)
        raise RemoteError(host)

    def json(self, method, url, *, payload=None, headers=None, retry=None):
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
            headers = {'Content-Type': 'application/json', **(headers or {})}
        result = self.request(method, url, body=body, headers=headers, retry=retry)
        try:
            return json.loads(result)
        except (ValueError, UnicodeError):
            raise RemoteError(urllib.parse.urlsplit(url).hostname) from None
