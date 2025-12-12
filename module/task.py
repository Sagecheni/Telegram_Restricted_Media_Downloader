# coding=UTF-8
# Author:Gentlesprite
# Software:PyCharm
# Time:2025/2/27 17:38
# File:task.py
import os
import asyncio

from functools import wraps
from typing import Union

import pyrogram

from module import console, log, APPDATA_PATH, yaml, CustomDumper
from module.language import _t
from module.stdio import MetaData
from module.enums import DownloadStatus, UploadStatus, KeyWord
from module.util import canonical_link_str, canonical_link_message


class DownloadTask:
    LINK_INFO: dict = {}
    COMPLETE_LINK: set = set()
    _HISTORY_FILE: str = os.path.join(APPDATA_PATH, "download_history.yaml")

    @staticmethod
    def _default_meta() -> dict:
        return {
            "link_type": None,
            "member_num": 0,
            "complete_num": 0,
            "file_name": set(),
            "error_msg": {},
        }

    @staticmethod
    def _load_history() -> None:
        try:
            if os.path.isfile(DownloadTask._HISTORY_FILE):
                with open(
                    file=DownloadTask._HISTORY_FILE, mode="r", encoding="UTF-8"
                ) as f:
                    data = yaml.safe_load(f) or {}
                items = data.get("complete_links") or []
                if isinstance(items, list):
                    normalized = []
                    for it in items:
                        s = str(it)
                        # 加载历史时也进行标准化，提升跨版本兼容性
                        try:
                            s = canonical_link_str(s)
                        except Exception:
                            pass
                        normalized.append(s)
                    DownloadTask.COMPLETE_LINK.update(normalized)
        except Exception:
            pass

    @staticmethod
    def _save_history() -> None:
        try:
            os.makedirs(APPDATA_PATH, exist_ok=True)
            payload = {"complete_links": sorted(list(DownloadTask.COMPLETE_LINK))}
            with open(file=DownloadTask._HISTORY_FILE, mode="w", encoding="UTF-8") as f:
                yaml.dump(payload, f, Dumper=CustomDumper)
        except Exception:
            pass

    def __init__(
        self,
        link: str,
        link_type: Union[str, None],
        member_num: int,
        complete_num: int,
        file_name: set,
        error_msg: dict,
    ):
        DownloadTask.LINK_INFO[link] = {
            "link_type": link_type,
            "member_num": member_num,
            "complete_num": complete_num,
            "file_name": file_name,
            "error_msg": error_msg,
        }

    def on_create_task(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            message_ids = kwargs.get("message_ids")
            link = message_ids
            if isinstance(message_ids, pyrogram.types.Message):
                # 统一使用规范化的任务键
                link = canonical_link_message(message_ids)
            elif isinstance(message_ids, str):
                # 统一标准化字符串链接（保留 single/comment 语义）
                link = canonical_link_str(message_ids)
            DownloadTask(
                link=link,
                link_type=None,
                member_num=0,
                complete_num=0,
                file_name=set(),
                error_msg={},
            )
            # 为不同形态的键建立别名映射，保证后续访问一致
            try:
                if isinstance(message_ids, pyrogram.types.Message):
                    mid = getattr(message_ids, "id", None)
                    if mid is not None:
                        # 将消息ID整型作为别名，指向同一元数据对象
                        DownloadTask.alias(alias_key=mid, primary_key=link)
            except Exception:
                pass
            res: dict = await func(self, *args, **kwargs)
            # 显式按键读取，避免依赖字典插入顺序
            chat_id = res.get("chat_id")
            link_type = res.get("link_type")
            member_num = res.get("member_num")
            status = res.get("status")
            e_code = res.get("e_code")
            if status == DownloadStatus.FAILURE:
                DownloadTask.set(link=link, key="error_msg", value=e_code)
                reason: str = e_code.get("error_msg")
                if reason:
                    log.error(
                        f"{_t(KeyWord.DOWNLOAD_TASK)}"
                        f'{_t(KeyWord.LINK)}:"{link}"{reason},'
                        f'{_t(KeyWord.REASON)}:"{e_code.get("all_member")}",'
                        f"{_t(KeyWord.STATUS)}:{_t(DownloadStatus.FAILURE)}。"
                    )
                else:
                    log.warning(
                        f"{_t(KeyWord.DOWNLOAD_TASK)}"
                        f'{_t(KeyWord.LINK)}:"{link}"{e_code.get("all_member")},'
                        f"{_t(KeyWord.STATUS)}:{_t(DownloadStatus.FAILURE)}。"
                    )
            elif status == DownloadStatus.DOWNLOADING:
                pass
            return res

        return wrapper

    def on_complete(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            res = func(self, *args, **kwargs)
            if all(i is None for i in res):
                return None
            link, file_name = res
            DownloadTask.add_file_name(link=link, file_name=file_name)
            for i in DownloadTask.LINK_INFO.items():
                compare_link: str = i[0]
                info: dict = i[1]
                if compare_link == link:
                    info["complete_num"] = len(info.get("file_name"))
            all_num: int = DownloadTask.get(link=link, key="member_num")
            complete_num: int = DownloadTask.get(link=link, key="complete_num")
            if all_num == complete_num:
                console.log(
                    f"{_t(KeyWord.DOWNLOAD_TASK)}"
                    f'{_t(KeyWord.LINK)}:"{link}",'
                    f"{_t(KeyWord.STATUS)}:{_t(DownloadStatus.SUCCESS)}。"
                )
                DownloadTask.set(link=link, key="error_msg", value={})
                DownloadTask.COMPLETE_LINK.add(link)
                asyncio.create_task(self.done_notice(f'"{link}"已下载完成。'))
                try:
                    DownloadTask._save_history()
                except Exception:
                    pass
            return res

        return wrapper

    @staticmethod
    def add_file_name(link, file_name):
        meta = DownloadTask.LINK_INFO.setdefault(link, DownloadTask._default_meta())
        files = meta.get("file_name")
        if isinstance(files, set):
            files.add(file_name)
        else:
            # 兼容性保护：如果被意外写成了列表，转换为集合
            new_set = set(files or [])
            new_set.add(file_name)
            meta["file_name"] = new_set

    @staticmethod
    def get(link: str, key: str) -> Union[str, int, set, dict, None]:
        return DownloadTask.LINK_INFO.get(link, {}).get(key)

    @staticmethod
    def set(link: str, key: str, value):
        meta = DownloadTask.LINK_INFO.setdefault(link, DownloadTask._default_meta())
        meta[key] = value

    @staticmethod
    def set_error(link: str, value, key: Union[str, None] = None):
        meta = DownloadTask.LINK_INFO.setdefault(link, DownloadTask._default_meta())
        errs = meta.setdefault("error_msg", {})
        errs[key if key else "all_member"] = value

    @staticmethod
    def alias(alias_key, primary_key) -> None:
        """将别名键映射到同一任务元数据对象。"""
        try:
            if primary_key in DownloadTask.LINK_INFO:
                DownloadTask.LINK_INFO[alias_key] = DownloadTask.LINK_INFO[primary_key]
        except Exception:
            pass


# 预加载历史已完成链接
try:
    DownloadTask._load_history()
except Exception:
    pass


class UploadTask:
    CHAT_ID_INFO: dict = {}

    def __init__(
        self,
        chat_id: Union[str, int],
        file_path: str,
        size: Union[str, int],
        error_msg: Union[str, None],
    ):
        if chat_id not in UploadTask.CHAT_ID_INFO:
            UploadTask.CHAT_ID_INFO[chat_id] = {}

        if file_path not in UploadTask.CHAT_ID_INFO[chat_id]:
            UploadTask.CHAT_ID_INFO[chat_id][file_path] = {}

        UploadTask.CHAT_ID_INFO.get(chat_id)[file_path] = {
            "size": size,
            "error_msg": error_msg,
        }

    def on_create_task(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            res: dict = await func(self, *args, **kwargs)
            # 返回结构为 {chat_id, file_name, size, status, error_msg}
            chat_id = res.get("chat_id")
            file_path = res.get(
                "file_name"
            )  # 对应返回中的 file_name 字段（实际为路径）
            size = res.get("size")
            status = res.get("status")
            e_code = res.get("error_msg")
            if status == UploadStatus.FAILURE:
                UploadTask.set_error_msg(
                    chat_id=chat_id, file_path=file_path, value=e_code
                )
                log.warning(
                    f"{_t(KeyWord.UPLOAD_TASK)}"
                    f'{_t(KeyWord.CHANNEL)}:"{chat_id}",'
                    f'{_t(KeyWord.FILE)}:"{file_path}",'
                    f"{_t(KeyWord.SIZE)}:{MetaData.suitable_units_display(size)},"
                    f'{_t(KeyWord.REASON)}:"{e_code}",'
                    f"{_t(KeyWord.STATUS)}:{_t(DownloadStatus.FAILURE)}。"
                )
            return res

        return wrapper

    @staticmethod
    def set_error_msg(chat_id: Union[str, int], file_path: str, value: str):
        meta: dict = UploadTask.CHAT_ID_INFO.get(chat_id)
        file_meta: dict = meta.get(
            file_path,
            {
                "size": os.path.getsize(file_path) if os.path.isfile(file_path) else 0,
                "error_msg": value,
            },
        )
        file_meta["error_msg"] = value
