import json
import unittest

from tw_stock_digest.analysis import analyze, analysis_key
from tw_stock_digest.drive import Drive


class QueueHTTP:
    """A scripted network boundary; records actual API arguments, no SDK mock."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return json.dumps(self.responses.pop(0)).encode()


class IntegrationTests(unittest.TestCase):
    def test_drive_partial_or_invalid_credentials_fail_closed(self):
        self.assertIsNone(Drive.from_env({}))
        for env in ({'GDRIVE_FOLDER_ID': 'folder'}, {'GDRIVE_OAUTH_JSON': 'secret-invalid'},
                    {'GDRIVE_FOLDER_ID': 'folder', 'GDRIVE_OAUTH_JSON': '[]'}):
            with self.assertRaises(ValueError) as error:
                Drive.from_env(env)
            self.assertNotIn('secret-invalid', str(error.exception))

    def test_existing_drive_file_is_updated_without_changing_parent(self):
        http = QueueHTTP([{'files': [{'id': 'existing'}]}, {'id': 'existing'}])
        drive = Drive('token', 'folder', http=http)
        self.assertEqual(drive.put('folder', 'report.md', b'new contents', 'text/markdown'), 'existing')
        method, url, kwargs = http.calls[-1]
        self.assertEqual(method, 'PATCH')
        self.assertIn('/existing?', url)
        self.assertEqual(kwargs['body'], b'new contents')
        self.assertIn('uploadType=media', url)

    def test_new_drive_file_contains_parent_and_content(self):
        http = QueueHTTP([{'files': []}, {'id': 'new-file'}])
        drive = Drive('token', 'folder', http=http)
        self.assertEqual(drive.put('folder', 'report.md', b'hello', 'text/markdown'), 'new-file')
        method, url, kwargs = http.calls[-1]
        self.assertEqual(method, 'POST')
        self.assertIn(b'"parents": ["folder"]', kwargs['body'])
        self.assertIn(b'hello', kwargs['body'])
        self.assertFalse(kwargs['retry'])

    def test_drive_detects_duplicates_across_pages(self):
        http = QueueHTTP([{'files': [{'id': 'one'}], 'nextPageToken': 'page2'},
                          {'files': [{'id': 'two'}]}])
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            Drive('token', 'folder', http=http).get('folder', 'report.md')
        self.assertIn('pageToken=page2', http.calls[-1][1])

    def test_analysis_key_is_stable_but_sensitive_to_model_and_data(self):
        a = analysis_key({'trade_date': '2026-10-21', 'watchlist': []}, 'model-a')
        self.assertEqual(a, analysis_key({'watchlist': [], 'trade_date': '2026-10-21'}, 'model-a'))
        self.assertNotEqual(a, analysis_key({'trade_date': '2026-10-22', 'watchlist': []}, 'model-a'))
        self.assertNotEqual(a, analysis_key({'trade_date': '2026-10-21', 'watchlist': []}, 'model-b'))

    def test_analysis_extracts_text_and_usage_without_paid_retry(self):
        http = QueueHTTP([{'content': [{'type': 'text', 'text': '每日摘要'}],
                           'stop_reason': 'end_turn', 'model': 'actual-model',
                           'usage': {'input_tokens': 123, 'output_tokens': 20}}])
        result = analyze({'trade_date': '2026-10-21'}, 'model', 'secret', http=http)
        self.assertEqual(result['text'], '每日摘要')
        self.assertEqual(result['usage']['input_tokens'], 123)
        self.assertEqual(result['model'], 'actual-model')
        self.assertFalse(http.calls[0][2]['retry'])
        self.assertNotIn('secret', json.dumps(result))

    def test_truncated_or_empty_analysis_is_not_saved_as_success(self):
        for response in ({'content': [{'type': 'text', 'text': 'partial'}], 'stop_reason': 'max_tokens'},
                         {'content': [], 'stop_reason': 'end_turn'}):
            with self.assertRaises(ValueError):
                analyze({}, 'model', 'secret', http=QueueHTTP([response]))
