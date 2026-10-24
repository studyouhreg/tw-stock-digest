#!/usr/bin/env python3
"""Run on your own computer once; credentials never go to terminal output."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tw_stock_digest.drive import Drive, SCOPE


def main():
    parser = argparse.ArgumentParser(description='以個人 Google 帳號授權本專案的 Drive 資料夾')
    parser.add_argument('--client-secret', type=Path, default=Path('client_secret.json'))
    parser.add_argument('--output', type=Path, default=Path('gdrive-oauth.json'))
    parser.add_argument('--folder-name', default='TW Stock Digest')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists. Choose another --output to preserve existing credentials.')
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        parser.error('Install setup dependencies: python -m pip install -r requirements-setup.txt')
    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secret), [SCOPE])
    creds = flow.run_local_server(host='127.0.0.1', port=0, access_type='offline', prompt='consent')
    if not creds.refresh_token:
        parser.error('No refresh token returned; repeat Google consent with offline access.')
    info = {key: getattr(creds, key) for key in ('client_id', 'client_secret', 'refresh_token')}
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(info, handle, indent=2)
    folder_id = Drive(creds.token, 'root').folder('root', args.folder_name)
    print(f'OAuth credentials saved to {args.output}. Keep this file private.')
    print(f'GDRIVE_FOLDER_ID={folder_id}')
    print('Upload the credential file contents to GitHub Secret GDRIVE_OAUTH_JSON, not to git or chat.')


if __name__ == '__main__':
    main()
