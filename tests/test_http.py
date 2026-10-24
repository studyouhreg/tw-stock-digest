import gzip
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tw_stock_digest.http import HttpClient, RemoteError


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.counts = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.counts[self.path] = cls.counts.get(self.path, 0) + 1
                if self.path == '/gzip':
                    body = gzip.compress(b'{"ok": true}')
                    self.send_response(200)
                    self.send_header('Content-Encoding', 'gzip')
                elif self.path == '/retry' and cls.counts[self.path] > 1:
                    body = b'{"recovered": true}'
                    self.send_response(200)
                else:
                    body = b'secret-must-not-leak'
                    self.send_response(503 if self.path == '/retry' else 401)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_GET

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_gzip_json(self):
        self.assertEqual(HttpClient().json('GET', self.base + '/gzip'), {'ok': True})

    def test_transient_get_retries(self):
        self.assertEqual(HttpClient(sleep=lambda _: None).json('GET', self.base + '/retry'),
                         {'recovered': True})

    def test_error_does_not_leak_body_or_query(self):
        with self.assertRaises(RemoteError) as error:
            HttpClient().request('GET', self.base + '/denied?token=secret-must-not-leak')
        self.assertNotIn('secret-must-not-leak', str(error.exception))
        self.assertEqual(error.exception.status, 401)

    def test_paid_post_not_automatically_retried(self):
        self.counts['/retry'] = 0
        with self.assertRaises(RemoteError):
            HttpClient(sleep=lambda _: None).json('POST', self.base + '/retry', payload={})
        self.assertEqual(self.counts['/retry'], 1)
