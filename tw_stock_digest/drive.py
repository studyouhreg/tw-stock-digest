"""OAuth personal-Drive storage using the app's drive.file scope."""
import json
import os
import re
import urllib.parse
import uuid

from .http import HttpClient

API = 'https://www.googleapis.com/drive/v3/files'
UPLOAD = 'https://www.googleapis.com/upload/drive/v3/files'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
SCOPE = 'https://www.googleapis.com/auth/drive.file'


def query_literal(value):
    return "'" + str(value).replace('\\', '\\\\').replace("'", "\\'") + "'"


class Drive:
    def __init__(self, token, root, *, http=None):
        self.headers = {'Authorization': f'Bearer {token}'}
        self.root = root
        self.http = http or HttpClient()

    @classmethod
    def from_env(cls, env=None, *, http=None):
        env = os.environ if env is None else env
        raw, root = env.get('GDRIVE_OAUTH_JSON', ''), env.get('GDRIVE_FOLDER_ID', '')
        if not raw and not root:
            return None
        if not raw or not root or not re.fullmatch(r'[A-Za-z0-9_-]+', root):
            raise ValueError('Set both GDRIVE_OAUTH_JSON and a valid GDRIVE_FOLDER_ID')
        try:
            info = json.loads(raw)
            if not isinstance(info, dict):
                raise ValueError()
            fields = {k: info[k] for k in ('client_id', 'client_secret', 'refresh_token')}
            if not all(isinstance(v, str) and v.strip() for v in fields.values()):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ValueError('Invalid GDRIVE_OAUTH_JSON; rerun scripts/setup_drive.py') from None
        http = http or HttpClient()
        body = urllib.parse.urlencode({**fields, 'grant_type': 'refresh_token'}).encode()
        response = http.request('POST', TOKEN_URL, body=body, retry=True,
                                headers={'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            token = json.loads(response)['access_token']
            if not isinstance(token, str) or not token:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ValueError('Google OAuth did not return an access token') from None
        return cls(token, root, http=http)

    def _find(self, parent, name):
        params = dict(q=f'{query_literal(parent)} in parents and name = {query_literal(name)} and trashed = false',
                      fields='nextPageToken,files(id,mimeType)', pageSize=100)
        matches = []
        while True:
            data = self.http.json('GET', API + '?' + urllib.parse.urlencode(params), headers=self.headers)
            matches.extend(data['files'])
            if len(matches) > 1:
                raise ValueError('Duplicate Drive filenames; keep one matching file/folder and retry')
            token = data.get('nextPageToken')
            if not token:
                return matches[0] if matches else None
            params['pageToken'] = token

    def folder(self, parent, name):
        existing = self._find(parent, name)
        if existing:
            if existing.get('mimeType') != 'application/vnd.google-apps.folder':
                raise ValueError('Drive folder name conflicts with an existing file')
            return existing['id']
        data = self.http.json('POST', API + '?fields=id', headers=self.headers,
                              payload={'name': name, 'mimeType': 'application/vnd.google-apps.folder',
                                       'parents': [parent]}, retry=False)
        return data['id']

    def get(self, parent, name):
        existing = self._find(parent, name)
        if not existing:
            return None
        return self.http.request('GET', API + '/' + existing['id'] + '?alt=media', headers=self.headers)

    def put(self, parent, name, content, mime):
        existing = self._find(parent, name)
        if existing:
            url = UPLOAD + '/' + existing['id'] + '?uploadType=media&fields=id'
            data = self.http.request('PATCH', url, body=content,
                                     headers={**self.headers, 'Content-Type': mime})
        else:
            boundary = 'twstock_' + uuid.uuid4().hex
            metadata = json.dumps({'name': name, 'parents': [parent]}, ensure_ascii=False).encode()
            body = (f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'.encode()
                    + metadata + f'\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n'.encode()
                    + content + f'\r\n--{boundary}--\r\n'.encode())
            data = self.http.request('POST', UPLOAD + '?uploadType=multipart&fields=id', body=body,
                                     headers={**self.headers, 'Content-Type': f'multipart/related; boundary={boundary}'},
                                     retry=False)
        return json.loads(data)['id']
