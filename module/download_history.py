# coding=UTF-8
# Author:Gentlesprite
# Software:PyCharm
# Time:2026/3/9 02:58
# File:download_history.py
import os
import sqlite3
import datetime

from typing import Union, Optional

from module import log
from module.language import _t
from module.enums import KeyWord


class DownloadHistory:
    FILE_NAME: str = 'download_history.sqlite3'

    def __init__(self, work_directory: str):
        self.work_directory: str = work_directory
        self.db_path: str = os.path.join(self.work_directory, self.FILE_NAME)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(self.work_directory, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                'CREATE TABLE IF NOT EXISTS completed_downloads ('
                'history_key TEXT PRIMARY KEY,'
                'chat_id TEXT,'
                'message_id INTEGER,'
                'media_group_id TEXT,'
                'download_type TEXT,'
                'link TEXT,'
                'file_path TEXT,'
                'file_size INTEGER,'
                'file_mtime REAL,'
                'file_sha256 TEXT,'
                'completed_at TEXT NOT NULL'
                ')'
            )
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_completed_downloads_media_group '
                'ON completed_downloads (chat_id, media_group_id)'
            )
            conn.execute(
                'CREATE TABLE IF NOT EXISTS completed_media_groups ('
                'group_key TEXT PRIMARY KEY,'
                'chat_id TEXT NOT NULL,'
                'media_group_id TEXT NOT NULL,'
                'member_num INTEGER,'
                'link TEXT,'
                'completed_at TEXT NOT NULL'
                ')'
            )

    def get_completed_download(self, history_key: str) -> Union[dict, None]:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM completed_downloads WHERE history_key = ?',
                (history_key,)
            ).fetchone()
        return dict(row) if row else None

    def remove_completed_download(self, history_key: str):
        with self._connect() as conn:
            conn.execute(
                'DELETE FROM completed_downloads WHERE history_key = ?',
                (history_key,)
            )

    def get_valid_completed_download(
            self,
            history_key: str,
            file_size: Optional[int] = None
    ) -> Union[dict, None]:
        record: Union[dict, None] = self.get_completed_download(history_key)
        if not record:
            return None
        file_path = record.get('file_path')
        if not file_path or not os.path.isfile(file_path):
            self.remove_completed_download(history_key)
            return None
        if file_size is not None and os.path.getsize(file_path) != file_size:
            self.remove_completed_download(history_key)
            return None
        return record

    def mark_completed_download(
            self,
            history_key: str,
            chat_id: Union[str, int, None],
            message_id: Optional[int],
            media_group_id: Union[str, int, None],
            download_type: Optional[str],
            link: Union[str, int, None],
            file_path: str,
            file_sha256: Union[str, None] = None
    ):
        file_size = os.path.getsize(file_path) if os.path.isfile(file_path) else None
        file_mtime = os.path.getmtime(file_path) if os.path.isfile(file_path) else None
        with self._connect() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO completed_downloads '
                '(history_key, chat_id, message_id, media_group_id, download_type, link, file_path, '
                'file_size, file_mtime, file_sha256, completed_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    history_key,
                    None if chat_id is None else str(chat_id),
                    message_id,
                    None if media_group_id is None else str(media_group_id),
                    download_type,
                    None if link is None else str(link),
                    file_path,
                    file_size,
                    file_mtime,
                    file_sha256,
                    datetime.datetime.now().isoformat(timespec='seconds')
                )
            )

    def is_media_group_completed(self, group_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT 1 FROM completed_media_groups WHERE group_key = ?',
                (group_key,)
            ).fetchone()
        return row is not None

    def mark_media_group_completed(
            self,
            group_key: str,
            chat_id: Union[str, int],
            media_group_id: Union[str, int],
            member_num: Optional[int] = None,
            link: Union[str, int, None] = None
    ):
        with self._connect() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO completed_media_groups '
                '(group_key, chat_id, media_group_id, member_num, link, completed_at) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (
                    group_key,
                    str(chat_id),
                    str(media_group_id),
                    member_num,
                    None if link is None else str(link),
                    datetime.datetime.now().isoformat(timespec='seconds')
                )
            )

    def safe_mark_completed_download(self, **kwargs):
        try:
            self.mark_completed_download(**kwargs)
        except Exception as e:
            log.warning(f'写入下载历史失败,{_t(KeyWord.REASON)}:"{e}"')

    def safe_mark_media_group_completed(self, **kwargs):
        try:
            self.mark_media_group_completed(**kwargs)
        except Exception as e:
            log.warning(f'写入媒体组历史失败,{_t(KeyWord.REASON)}:"{e}"')
