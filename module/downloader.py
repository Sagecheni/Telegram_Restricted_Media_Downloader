# coding=UTF-8
# Author:Gentlesprite
# Software:PyCharm
# Time:2023/10/3 1:00:03
# File:downloader.py
import asyncio
import datetime
import json
import os
import re
import shutil
import sys
import time
from functools import partial
from sqlite3 import OperationalError
from typing import Callable, Dict, Iterable, Optional, Tuple, Union

import aiohttp
import pyrogram
import yt_dlp
from pyrogram.enums.parse_mode import ParseMode
from pyrogram.errors.exceptions.bad_request_400 import (
    BotMethodInvalid,
    ChannelInvalid,
)
from pyrogram.errors.exceptions.bad_request_400 import (
    ChannelPrivate as ChannelPrivate_400,
)
from pyrogram.errors.exceptions.bad_request_400 import (
    ChatForwardsRestricted as ChatForwardsRestricted_400,
)
from pyrogram.errors.exceptions.bad_request_400 import (
    MsgIdInvalid,
    PeerIdInvalid,
    UsernameInvalid,
    UsernameNotOccupied,
)
from pyrogram.errors.exceptions.forbidden_403 import ChatWriteForbidden
from pyrogram.errors.exceptions.not_acceptable_406 import (
    ChannelPrivate as ChannelPrivate_406,
)
from pyrogram.errors.exceptions.not_acceptable_406 import (
    ChatForwardsRestricted as ChatForwardsRestricted_406,
)
from pyrogram.errors.exceptions.unauthorized_401 import (
    AuthKeyUnregistered,
    SessionExpired,
    SessionRevoked,
    Unauthorized,
)
from pyrogram.handlers import MessageHandler
from pyrogram.types.bots_and_keyboards import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.types.messages_and_media import ReplyParameters

from module import LINK_PREVIEW_OPTIONS, SLEEP_THRESHOLD, console, log
from module.app import Application
from module.bot import Bot, CallbackData, KeyboardButton
from module.enums import (
    BotButton,
    BotCallbackText,
    BotMessage,
    CalenderKeyboard,
    DownloadStatus,
    DownloadType,
    KeyWord,
    LinkType,
    SaveDirectoryPrefix,
)
from module.filter import Filter
from module.language import _t
from module.path_tool import (
    compare_file_size,
    get_file_size,
    is_file_duplicate,
    move_to_save_directory,
    safe_delete,
    safe_replace,
    split_path,
)
from module.stdio import Base64Image, MetaData, ProgressBar
from module.task import DownloadTask
from module.uploader import TelegramUploader
from module.util import (
    Issues,
    canonical_link_message,
    canonical_link_str,
    format_chat_link,
    get_chat_with_notify,
    get_message_by_link,
    parse_link,
    safe_message,
    truncate_display_filename,
)


class TelegramProgressTracker:
    """管理 Telegram 消息中的下载进度显示"""

    def __init__(self, client: pyrogram.Client, chat_id: int, update_interval: float = 2.0):
        """
        初始化 Telegram 进度追踪器

        Args:
            client: Pyrogram 客户端
            chat_id: 聊天 ID
            update_interval: 更新间隔（秒），默认 2 秒
        """
        self.client = client
        self.chat_id = chat_id
        self.progress_messages: Dict[str, pyrogram.types.Message] = {}
        self.last_update_time: Dict[str, float] = {}
        self.update_interval = update_interval
        self.last_bytes: Dict[str, int] = {}  # 用于计算速度
        self.last_speed_time: Dict[str, float] = {}

    async def create_progress_message(
        self, task_id: str, filename: str
    ) -> Optional[pyrogram.types.Message]:
        """
        创建进度消息

        Args:
            task_id: 任务 ID
            filename: 文件名

        Returns:
            创建的消息对象，如果失败返回 None
        """
        try:
            text = self._format_progress_text(filename, 0, 0, 0)
            message = await self.client.send_message(self.chat_id, text)
            self.progress_messages[task_id] = message
            self.last_update_time[task_id] = time.time()
            self.last_bytes[task_id] = 0
            self.last_speed_time[task_id] = time.time()
            return message
        except Exception as e:
            log.warning(f'创建进度消息失败: {e}')
            return None

    async def update_progress(
        self,
        task_id: str,
        filename: str,
        current: int,
        total: int,
    ) -> None:
        """
        更新进度（带节流控制）

        Args:
            task_id: 任务 ID
            filename: 文件名
            current: 当前已下载字节数
            total: 总字节数
        """
        current_time = time.time()

        # 节流：仅在距离上次更新超过 update_interval 时才更新
        if task_id in self.last_update_time:
            if current_time - self.last_update_time[task_id] < self.update_interval:
                return

        # 计算速度
        speed = 0.0
        if task_id in self.last_bytes and task_id in self.last_speed_time:
            time_diff = current_time - self.last_speed_time[task_id]
            if time_diff > 0:
                bytes_diff = current - self.last_bytes[task_id]
                speed = bytes_diff / time_diff

        if task_id in self.progress_messages:
            text = self._format_progress_text(filename, current, total, speed)
            try:
                await self.client.edit_message_text(
                    self.chat_id, self.progress_messages[task_id].id, text
                )
                self.last_update_time[task_id] = current_time
                self.last_bytes[task_id] = current
                self.last_speed_time[task_id] = current_time
            except Exception as e:
                # 忽略消息编辑失败（可能是消息被删除或频率限制）
                log.debug(f"更新进度消息失败: {e}")

    async def complete_progress(
        self, task_id: str, filename: str, success: bool = True
    ) -> None:
        """
        标记完成

        Args:
            task_id: 任务 ID
            filename: 文件名
            success: 是否成功
        """
        if task_id in self.progress_messages:
            status = "✅ 下载完成" if success else "❌ 下载失败"
            text = f"{status}\n📁 文件: {truncate_display_filename(filename)}"
            try:
                await self.client.edit_message_text(
                    self.chat_id, self.progress_messages[task_id].id, text
                )
            except Exception as e:
                log.debug(f"更新完成消息失败: {e}")
            finally:
                # 清理
                self.progress_messages.pop(task_id, None)
                self.last_update_time.pop(task_id, None)
                self.last_bytes.pop(task_id, None)
                self.last_speed_time.pop(task_id, None)

    def _format_progress_text(
        self, filename: str, current: int, total: int, speed: float
    ) -> str:
        """
        格式化进度文本

        Args:
            filename: 文件名
            current: 当前字节数
            total: 总字节数
            speed: 下载速度（字节/秒）

        Returns:
            格式化的进度文本
        """
        if total > 0:
            percentage = (current / total) * 100
            bar_length = 20
            filled = int(bar_length * current / total)
            bar = "█" * filled + "░" * (bar_length - filled)

            current_str = MetaData.suitable_units_display(current)
            total_str = MetaData.suitable_units_display(total)
            speed_str = (
                f"{MetaData.suitable_units_display(int(speed))}/s"
                if speed > 0
                else "计算中..."
            )

            return (
                f"📥 下载中...\n"
                f"📁 {truncate_display_filename(filename)}\n"
                f"[{bar}] {percentage:.1f}%\n"
                f"📊 {current_str} / {total_str}\n"
                f"⚡️ {speed_str}"
            )
        else:
            return f"📥 正在准备下载...\n📁 {truncate_display_filename(filename)}"


class TelegramRestrictedMediaDownloader(Bot):
    def __init__(self):
        super().__init__()
        self.loop = asyncio.get_event_loop()
        self.event = asyncio.Event()
        self.queue = asyncio.Queue()
        self.app = Application()
        self.is_running: bool = False
        self.running_log: set = set()
        self.running_log.add(self.is_running)
        self.pb = ProgressBar()
        self.uploader: Union[TelegramUploader, None] = None
        self.cd: Union[CallbackData, None] = None
        # 标签映射: 链接->标签、(chat_id,message_id)->标签、监听(chat_id)->标签
        self.link_tag_map: Dict[str, str] = {}
        self.message_tag_map: Dict[tuple, str] = {}
        self.listen_download_tag_by_chatid: Dict[Union[int, str], str] = {}
        # 规范化后的进行中/已分配链接集合（仅用于去重判断）
        self.bot_task_link_canon: set = set()
        # gallery-dl 配置
        base_dir = getattr(
            self.app,
            "DIRECTORY_NAME",
            os.path.dirname(os.path.abspath(sys.argv[0])),
        )
        self.gallery_dl_base_dir: str = base_dir
        self.gallery_dl_config_path: str = os.path.join(
            self.gallery_dl_base_dir, "config", "gallery-dl", "config.json"
        )
        self.gallery_dl_config: Union[dict, None] = None
        self._load_gallery_dl_config()
        # Telegram 进度追踪器（每个 chat_id 一个追踪器）
        self.telegram_progress_trackers: Dict[int, TelegramProgressTracker] = {}

    def _load_gallery_dl_config(self) -> None:
        try:
            if os.path.isfile(self.gallery_dl_config_path):
                with open(self.gallery_dl_config_path, "r", encoding="UTF-8") as f:
                    self.gallery_dl_config = json.load(f)
                log.info(
                    f'已加载 gallery-dl 配置文件:"{self.gallery_dl_config_path}"。'
                )
            else:
                log.warning(
                    f'未找到 gallery-dl 配置文件:"{self.gallery_dl_config_path}"。'
                )
        except Exception as e:
            self.gallery_dl_config = None
            log.error(f'加载 gallery-dl 配置文件失败,{_t(KeyWord.REASON)}:"{e}"')

    async def _run_gallery_dl(
        self,
        url: str,
        site: str,
    ) -> bool:
        """使用 gallery-dl 下载指定站点链接。

        返回值:
            True  - gallery-dl 认为下载成功(退出码为0)。
            False - 运行失败或退出码非0。
        """
        # 优先尝试通过 PATH 中的 gallery-dl
        executable = shutil.which("gallery-dl") or "gallery-dl"
        cmd: list = [executable]

        if self.gallery_dl_config_path and os.path.isfile(self.gallery_dl_config_path):
            cmd.extend(["--config", self.gallery_dl_config_path])

        cmd.append(url)
        log.info(f'使用 gallery-dl 下载{site}链接:"{url}"，命令:{cmd}')

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.gallery_dl_base_dir,
            )

            async def _log_stream(stream, is_stderr: bool = False) -> None:
                """实时读取并记录子进程输出。.

                说明:
                - gallery-dl 的进度条通常通过带 \\r 的单行刷新输出;
                  如果仅按 readline() 等待 \\n, 进度信息会被“憋”到进程结束才刷出。
                - 这里按块读取, 同时把 \\r 视作换行边界, 以便在日志中看到实时进度。
                """
                if stream is None:
                    return
                buffer = ""
                while True:
                    chunk = await stream.read(1024)
                    if not chunk:
                        break
                    text = chunk.decode(errors="ignore")
                    if not text:
                        continue
                    buffer += text
                    buffer = buffer.replace("\r", "\n")
                    lines = buffer.split("\n")
                    buffer = lines[-1]
                    for line in lines[:-1]:
                        line = line.strip()
                        if not line:
                            continue
                        # 将 stderr 输出也视作 INFO 级别日志, 以便统一查看进度
                        if is_stderr:
                            log.info(f"[gallery-dl][stderr] {line}")
                        else:
                            log.info(f"[gallery-dl] {line}")
                # flush 剩余缓冲
                buffer = buffer.strip()
                if buffer:
                    if is_stderr:
                        log.info(f"[gallery-dl][stderr] {buffer}")
                    else:
                        log.info(f"[gallery-dl] {buffer}")

            # 实时读取 stdout/stderr，避免一次性缓冲导致的延迟
            await asyncio.gather(
                _log_stream(proc.stdout, is_stderr=False),
                _log_stream(proc.stderr, is_stderr=True),
            )
            await proc.wait()

            if proc.returncode == 0:
                log.info(f'gallery-dl 下载成功({site}):"{url}"')
                return True
            log.warning(f'gallery-dl 下载失败({site}),退出码:{proc.returncode},"{url}"')
            return False
        except FileNotFoundError:
            log.error("未找到 gallery-dl 可执行文件,请确认已正确安装。")
        except Exception as e:
            log.exception(
                f'运行 gallery-dl 时发生异常({site}),链接:"{url}",{_t(KeyWord.REASON)}:"{e}"'
            )
        return False

    async def _download_ranking_video(
        self, url: str, message: pyrogram.types.Message
    ) -> bool:
        """下载 twitter-ero-video-ranking.com 视频 (支持直接 mp4 链接)"""
        try:
            mp4_url = ""
            if "video.twimg.com" in url:
                # Direct MP4 link provided
                mp4_url = url
                log.info(f"检测到直接视频链接: {mp4_url}")
            else:
                # 1. Manually fetch HTML to find the video link
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Referer": "https://twitter.com/",
                }
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers) as response:
                        if response.status != 200:
                            log.error(f"请求排行榜页面失败: {url}, status={response.status}")
                            return False
                        html = await response.text()

                # 2. Extract MP4 link manually (to handle resolution variations robustly)
                mp4_pattern = r'href="([^"]+\.mp4[^"]*)"'
                match = re.search(mp4_pattern, html)
                if not match:
                    log.warning(f"未找到 MP4 链接: {url}")
                    return False

                mp4_url = match.group(1)
                log.info(f"解析到视频链接: {mp4_url}")

            video_id = mp4_url.split("/")[-1].split("?")[0]  # Ensure query params are stripped from filename

            # 构建保存路径
            base_save_dir = self.env_save_directory(message)
            save_dir = os.path.join(base_save_dir, "TwitterRanking")
            if not os.path.exists(save_dir):
                os.makedirs(save_dir)

            # 输出文件名模板 (yt-dlp 风格)
            output_template = os.path.join(save_dir, f"{video_id}.%(ext)s")

            # 检查文件是否已存在 (简单检查 mp4)
            expected_file = os.path.join(save_dir, f"{video_id}.mp4")
            if os.path.exists(expected_file):
                log.info(f"文件已存在，跳过: {expected_file}")
                return True

            log.info(f"开始使用 yt-dlp 下载排行榜视频: {mp4_url}")

            def run_yt_dlp():
                ydl_opts = {
                    "outtmpl": output_template,
                    "format": "bestvideo+bestaudio/best",
                    "merge_output_format": "mp4",
                    "quiet": True,
                    "no_warnings": True,
                    "socket_timeout": 60,
                    "http_headers": {
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Referer": "https://twitter.com/",
                    },
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([mp4_url])

            # 在执行器中运行同步的 yt-dlp
            await self.loop.run_in_executor(None, run_yt_dlp)

            log.info(f"下载成功: {expected_file}")
            return True

        except Exception as e:
            log.exception(f"下载排行榜视频出错 (yt-dlp): {url}, 原因: {e}")
            return False

    def env_save_directory(self, message: pyrogram.types.Message) -> str:
        save_directory = self.app.save_directory
        for placeholder in SaveDirectoryPrefix():
            if placeholder in save_directory:
                if placeholder == SaveDirectoryPrefix.CHAT_ID:
                    save_directory = save_directory.replace(
                        placeholder,
                        str(getattr(getattr(message, "chat"), "id", "UNKNOWN_CHAT_ID")),
                    )
                if placeholder == SaveDirectoryPrefix.MIME_TYPE:
                    for dtype in DownloadType():
                        if getattr(message, dtype, None):
                            save_directory = save_directory.replace(placeholder, dtype)
        # 附加标签子目录(优先级: 单条消息标签 > 监听频道标签)
        try:
            chat_id = getattr(getattr(message, "chat", None), "id", None)
            mid = getattr(message, "id", None)
            tag = None
            if chat_id is not None and mid is not None:
                tag = self.message_tag_map.get((chat_id, mid))
            if tag is None and chat_id is not None:
                tag = self.listen_download_tag_by_chatid.get(chat_id)
            if isinstance(tag, str) and tag.strip():
                from module.path_tool import validate_title

                save_directory = os.path.join(
                    save_directory, validate_title(tag.strip())
                )
        except Exception:
            pass
        return save_directory

    async def get_download_link_from_bot(
        self,
        client: pyrogram.Client,
        message: pyrogram.types.Message,
        with_upload: Union[dict, None] = None,
    ):
        link_meta: Union[dict, None] = await super().get_download_link_from_bot(
            client, message
        )

        # 当父类无法解析(非 t.me 链接)时，尝试处理外部链接(X/Twitter、Instagram、Iwara)
        if link_meta is None:
            text = (message.text or "").strip()
            # 提取 /download 后的参数
            parts = text.split()
            if parts and parts[0] == "/download":
                parts = parts[1:]

            # 识别不同站点链接
            x_patterns: Tuple[str, ...] = (
                r"https?://(?:www\.)?x\.com/[^\s]+",
                r"https?://(?:mobile\.)?twitter\.com/[^\s]+",
                r"https?://t\.co/[^\s]+",
            )
            ig_patterns: Tuple[str, ...] = (
                r"https?://(?:www\.)?instagram\.com/[^\s]+",
                r"https?://(?:www\.)?instagr\.am/[^\s]+",
            )
            iwara_patterns: Tuple[str, ...] = (r"https?://(?:www\.)?iwara\.tv/[^\s]+",)

            def _collect(  # type: ignore[return-type]
                tokens: Iterable[str], patterns: Tuple[str, ...]
            ) -> set:
                result: set = set()
                for token in tokens:
                    for pat in patterns:
                        if re.match(pat, token):
                            result.add(token)
                            break
                return result

            x_links: set = _collect(parts, x_patterns)
            ig_links: set = _collect(parts, ig_patterns)
            iwara_links: set = _collect(parts, iwara_patterns)

            # 末尾追加标签(与 t.me 分支一致的 UX):
            # /download url1 [url2 ...] [标签]
            tag: Union[str, None] = None
            if parts:
                last_token = parts[-1]
                if not any(
                    re.match(p, last_token)
                    for p in (*x_patterns, *ig_patterns, *iwara_patterns)
                ):
                    tag = last_token

            # 若既不是 t.me 链接，又没有识别到外部站点，交由后续逻辑处理
            if not x_links and not ig_links and not iwara_links:
                # 检查是否为 twitter-ero-video-ranking.com 链接 或 video.twimg.com 直接链接
                ranking_pattern = r"https?://(?:www\.)?twitter-ero-video-ranking\.com/zh-CN/movie/([a-zA-Z0-9_-]+)"
                direct_twimg_pattern = r"https?://video\.twimg\.com/.*\.mp4.*"
                
                ranking_links = []
                for p in parts:
                    if re.match(ranking_pattern, p) or re.match(direct_twimg_pattern, p):
                        ranking_links.append(p)

                if ranking_links:
                    status_msg = await self.safe_process_message(
                        client=client,
                        message=message,
                        text=[
                            f"🔄 检测到排行榜链接，正在下载 {len(ranking_links)} 个视频..."
                        ],
                    )
                    success_count = 0
                    fail_links = []

                    for link in ranking_links:
                        if await self._download_ranking_video(link, message):
                            success_count += 1
                        else:
                            fail_links.append(link)

                    summary = [f"✅ 排行榜视频下载完成: 成功 {success_count} 个"]
                    if fail_links:
                        summary.append("❌ 以下链接下载失败:")
                        summary.extend(fail_links)

                    await self.safe_edit_message(
                        client=client,
                        message=message,
                        last_message_id=status_msg.id,
                        text="\n".join(summary),
                    )
                    return None

                return None

            # 1. 先尝试通过 gallery-dl 下载所有外部链接
            total_x = len(x_links)
            total_ig = len(ig_links)
            total_iwara = len(iwara_links)
            status_lines = [
                "🔄 检测到外部链接，正在通过 gallery-dl 下载…",
            ]
            if total_x:
                status_lines.append(f"• X/Twitter: {total_x} 条")
            if total_ig:
                status_lines.append(f"• Instagram: {total_ig} 条")
            if total_iwara:
                status_lines.append(f"• Iwara: {total_iwara} 条")

            status_msg = await self.safe_process_message(
                client=client,
                message=message,
                text=status_lines,
            )

            gd_success_x: list = []
            gd_fail_x: list = []
            gd_success_ig: list = []
            gd_fail_ig: list = []
            gd_success_iw: list = []
            gd_fail_iw: list = []

            # 串行处理，避免对站点造成过大压力
            for url in x_links:
                if await self._run_gallery_dl(url=url, site="X/Twitter"):
                    gd_success_x.append(url)
                else:
                    gd_fail_x.append(url)
            for url in ig_links:
                if await self._run_gallery_dl(url=url, site="Instagram"):
                    gd_success_ig.append(url)
                else:
                    gd_fail_ig.append(url)
            for url in iwara_links:
                if await self._run_gallery_dl(url=url, site="Iwara"):
                    gd_success_iw.append(url)
                else:
                    gd_fail_iw.append(url)

            # 2. 对 gallery-dl 失败的 X/Twitter 链接走“转发机器人”回退逻辑
            converter_success = 0
            converter_fail: list = []

            if gd_fail_x:
                converter_cfg: dict = (
                    self.app.config.get("converter", {})
                    if isinstance(self.app.config, dict)
                    else {}
                )
                if not converter_cfg.get("enabled"):
                    log.warning(
                        f"gallery-dl 无法处理以下 X/Twitter 链接，且未启用转换机器人回退: {gd_fail_x}"
                    )
                else:
                    bot_username: Union[str, None] = converter_cfg.get("bot_username")
                    timeout: int = int(converter_cfg.get("timeout") or 180)
                    if not bot_username:
                        log.warning(
                            "gallery-dl 处理 X/Twitter 失败且未配置 converter.bot_username，"
                            f"失败链接: {gd_fail_x}"
                        )
                    else:
                        log.info(
                            f"gallery-dl 下载失败,启用回退转换机器人 {bot_username} 处理 X/Twitter 链接。"
                        )
                        for url in gd_fail_x:
                            try:
                                media_msg = await self.fetch_from_converter(
                                    url=url, converter=bot_username, timeout=timeout
                                )
                                if isinstance(media_msg, list):
                                    for m in media_msg:
                                        if tag:
                                            try:
                                                _cid = getattr(
                                                    getattr(m, "chat", None), "id", None
                                                )
                                                _mid = getattr(m, "id", None)
                                                if (
                                                    _cid is not None
                                                    and _mid is not None
                                                ):
                                                    self.message_tag_map[
                                                        (_cid, _mid)
                                                    ] = tag
                                            except Exception:
                                                pass
                                        await self.create_download_task(
                                            message_ids=m,
                                            with_upload=with_upload,
                                            single_link=True,
                                        )
                                        converter_success += 1
                                else:
                                    if tag:
                                        try:
                                            _cid = getattr(
                                                getattr(media_msg, "chat", None),
                                                "id",
                                                None,
                                            )
                                            _mid = getattr(media_msg, "id", None)
                                            if _cid is not None and _mid is not None:
                                                self.message_tag_map[(_cid, _mid)] = tag
                                        except Exception:
                                            pass
                                    await self.create_download_task(
                                        message_ids=media_msg,
                                        with_upload=with_upload,
                                        single_link=True,
                                    )
                                    converter_success += 1
                            except Exception as e:
                                log.warning(
                                    f'X链接转换失败(作为 gallery-dl 回退):"{url}"，原因:{e}'
                                )
                                converter_fail.append(url)

            # 3. 汇总提示
            summary: list = []
            if gd_success_x or gd_success_ig or gd_success_iw:
                summary.append("✅ gallery-dl 下载完成概览:")
                if gd_success_x:
                    summary.append(f"• X/Twitter 成功 {len(gd_success_x)} 条")
                if gd_success_ig:
                    summary.append(f"• Instagram 成功 {len(gd_success_ig)} 条")
                if gd_success_iw:
                    summary.append(f"• Iwara 成功 {len(gd_success_iw)} 条")
            if gd_fail_ig or gd_fail_iw:
                summary.append(
                    "⚠️ 以下链接 gallery-dl 下载失败(未配置回退逻辑，仅记录):"
                )
                summary.extend(gd_fail_ig + gd_fail_iw)
            if gd_fail_x:
                summary.append("⚠️ 以下 X/Twitter 链接 gallery-dl 下载失败:")
                summary.extend(gd_fail_x)
            if converter_success:
                summary.append(
                    f"✅ 已通过转换机器人提交 {converter_success} 个 X/Twitter 媒体到下载队列。"
                )
            if converter_fail:
                summary.append(
                    "❌ 以下 X/Twitter 链接在转换机器人回退中仍然失败(请确认已在转换机器人处 /start)："
                )
                summary.extend(converter_fail)
            if not summary:
                summary.append("ℹ️ 未找到可处理的外部链接或所有下载均已失败。")

            await self.safe_edit_message(
                client=client,
                message=message,
                last_message_id=status_msg.id,
                text="\n".join(summary),
            )
            return None
        right_link: set = link_meta.get("right_link")
        invalid_link: set = link_meta.get("invalid_link")
        last_bot_message: Union[pyrogram.types.Message, None] = link_meta.get(
            "last_bot_message"
        )
        tag: Union[str, None] = link_meta.get("tag")
        # 规范化用于去重的键
        right_link_canon: set = {canonical_link_str(l) for l in (right_link or set())}
        # 记录链接级别的标签, 在后续创建任务时映射到具体消息
        if tag:
            for rl in list(right_link or []):
                try:
                    self.link_tag_map[rl] = tag
                except Exception:
                    pass
        # 命中“进行中/已分配”或“已完成”的规范化键
        existed_canon = set()
        existed_canon.update(
            {c for c in right_link_canon if c in self.bot_task_link_canon}
        )
        existed_canon.update(
            {c for c in right_link_canon if c in DownloadTask.COMPLETE_LINK}
        )
        # 将规范化命中映射回原字符串用于展示
        canon_map = {canonical_link_str(s): s for s in (right_link or set())}
        exist_link = set()
        for c in existed_canon:
            if c in canon_map:
                exist_link.add(canon_map[c])
        right_link -= exist_link
        right_link_canon -= existed_canon
        if last_bot_message:
            await self.safe_edit_message(
                client=client,
                message=message,
                last_message_id=last_bot_message.id,
                text=self.update_text(
                    right_link=right_link,
                    exist_link=exist_link,
                    invalid_link=invalid_link,
                ),
            )
        else:
            log.warning("消息过长编辑频繁,暂时无法通过机器人显示通知。")
        links: Union[set, None] = self.__process_links(link=list(right_link))

        if links is None:
            return None
        for link in links:
            task: dict = await self.create_download_task(
                message_ids=link, retry=None, with_upload=with_upload
            )
            if task.get("status") == DownloadStatus.FAILURE:
                invalid_link.add(link)
            else:
                self.bot_task_link.add(link)
                try:
                    self.bot_task_link_canon.add(canonical_link_str(link))
                except Exception:
                    pass
        right_link -= invalid_link
        await self.safe_edit_message(
            client=client,
            message=message,
            last_message_id=last_bot_message.id,
            text=self.update_text(
                right_link=right_link, exist_link=exist_link, invalid_link=invalid_link
            ),
        )

    async def fetch_from_converter(
        self, url: str, converter: str, timeout: int = 180
    ) -> Union[pyrogram.types.Message, list]:
        """将X/Twitter链接发送至指定转换机器人并等待媒体返回。"""
        conv = converter if converter.startswith("@") else f"@{converter}"

        # 获取发送前最新消息ID
        last_id = 0
        try:
            async for m in self.app.client.get_chat_history(conv, limit=1):
                last_id = max(last_id, getattr(m, "id", 0))
        except Exception:
            # 可能首次对话，需要 /start；交由后续流程报错提示
            pass

        # 发送链接
        await self.app.client.send_message(conv, url)

        # 轮询等待新媒体消息
        start_ts = datetime.datetime.now().timestamp()
        collected: list = []
        seen: set = set()
        while datetime.datetime.now().timestamp() - start_ts < timeout:
            try:
                async for m in self.app.client.get_chat_history(conv, limit=10):
                    mid = getattr(m, "id", 0)
                    if mid <= last_id or mid in seen:
                        continue
                    seen.add(mid)
                    from_user = getattr(m, "from_user", None)
                    if from_user and getattr(from_user, "is_bot", False):
                        # 命中媒体
                        if any(getattr(m, dtype, None) for dtype in DownloadType()):
                            collected.append(m)
                if collected:
                    # 若有多条媒体，一并返回
                    return collected[0] if len(collected) == 1 else collected
            except Exception:
                pass
            await asyncio.sleep(2)

        raise TimeoutError("等待转换机器人返回超时")

    async def get_upload_link_from_bot(
        self,
        client: pyrogram.Client,
        message: pyrogram.types.Message,
        delete: bool = False,
        save_directory: str = None,
    ):
        link_meta: Union[dict, None] = await super().get_upload_link_from_bot(
            client, message
        )
        if link_meta is None:
            return None
        file_path: str = link_meta.get("file_path")
        target_link: str = link_meta.get("target_link")
        try:
            await self.uploader.create_upload_task(
                link=target_link, file_path=file_path
            )
        except ValueError:
            await client.send_message(
                chat_id=message.from_user.id,
                reply_parameters=ReplyParameters(message_id=message.id),
                text=f"⬇️⬇️⬇️目标频道不存在⬇️⬇️⬇️\n{target_link}",
            )

    async def start(self, client: pyrogram.Client, message: pyrogram.types.Message):
        self.last_client: pyrogram.Client = client
        self.last_message: pyrogram.types.Message = message
        chat_id = message.from_user.id
        # 简化欢迎信息: 仅保留机器人加载成功提示 + 可用命令列表
        await client.send_message(
            chat_id=chat_id,
            text="🐵🐵🐵机器人加载成功!🐵🐵🐵",
            link_preview_options=LINK_PREVIEW_OPTIONS,
        )
        # 继续输出帮助信息(含「可用命令」与「设置」按钮), 但不再附带赞助图片/按钮
        await super().start(client, message)

    async def callback_data(
        self, client: pyrogram.Client, callback_query: pyrogram.types.CallbackQuery
    ):
        callback_data = await super().callback_data(client, callback_query)
        kb = KeyboardButton(callback_query)
        if callback_data is None:
            return None
        elif callback_data == BotCallbackText.NOTICE:
            try:
                self.gc.config[BotCallbackText.NOTICE] = not self.gc.config.get(
                    BotCallbackText.NOTICE
                )
                self.gc.save_config(self.gc.config)
                n_s: str = (
                    "启用" if self.gc.config.get(BotCallbackText.NOTICE) else "禁用"
                )
                n_p: str = f"机器人消息通知已{n_s}。"
                log.info(n_p)
                console.log(n_p, style="#FF4689")
                await kb.toggle_setting_button(
                    global_config=self.gc.config, user_config=self.app.config
                )
            except Exception as e:
                await callback_query.message.reply_text(
                    "启用或禁用机器人消息通知失败\n(具体原因请前往终端查看报错信息)"
                )
                log.error(f'启用或禁用机器人消息通知失败,{_t(KeyWord.REASON)}:"{e}"')
        elif callback_data == BotCallbackText.PAY:
            res: Union[str, None] = await self.__send_pay_qr(
                client=client,
                chat_id=callback_query.from_user.id,  # v1.6.5 修复发送图片时chat_id错误问题。
                load_name="收款码",
            )
            MetaData.pay()
            if res:
                msg = "🥰🥰🥰\n收款「二维码」已发送至您的「终端」十分感谢您的支持!"
            else:
                msg = "🥰🥰🥰\n收款「二维码」已发送至您的「终端」与「对话框」十分感谢您的支持!"
            await callback_query.message.reply_text(msg)
        elif callback_data == BotCallbackText.BACK_HELP:
            meta: dict = await self.help()
            await callback_query.message.edit_text(meta.get("text"))
            await callback_query.message.edit_reply_markup(meta.get("keyboard"))
        elif callback_data == BotCallbackText.BACK_TABLE:
            meta: dict = await self.table()
            await callback_query.message.edit_text(meta.get("text"))
            await callback_query.message.edit_reply_markup(meta.get("keyboard"))
        elif callback_data in (
            BotCallbackText.DOWNLOAD,
            BotCallbackText.DOWNLOAD_UPLOAD,
        ):
            if not isinstance(self.cd.data, dict):
                return None
            meta: Union[dict, None] = self.cd.data.copy()
            self.cd.data = None
            origin_link: str = meta.get("origin_link")
            target_link: str = meta.get("target_link")
            start_id: Union[int, None] = meta.get("start_id")
            end_id: Union[int, None] = meta.get("end_id")
            if callback_data == BotCallbackText.DOWNLOAD:
                self.last_message.text = f"/download {origin_link} {start_id} {end_id}"
                await self.get_download_link_from_bot(
                    client=self.last_client, message=self.last_message
                )
            elif callback_data == BotCallbackText.DOWNLOAD_UPLOAD:
                self.last_message.text = f"/download {origin_link} {start_id} {end_id}"
                await self.get_download_link_from_bot(
                    client=self.last_client,
                    message=self.last_message,
                    with_upload={
                        "link": target_link,
                        "file_name": None,
                        "with_delete": False,
                    },
                )
            await kb.task_assign_button()
        elif callback_data == BotCallbackText.LOOKUP_LISTEN_INFO:
            await self.app.client.send_message(
                chat_id=callback_query.message.from_user.id,
                text="/listen_info",
                link_preview_options=LINK_PREVIEW_OPTIONS,
            )
        elif callback_data == BotCallbackText.SHUTDOWN:
            try:
                self.app.config["is_shutdown"] = not self.app.config.get("is_shutdown")
                self.app.save_config(self.app.config)
                s_s: str = "启用" if self.app.config.get("is_shutdown") else "禁用"
                s_p: str = f"退出后关机已{s_s}。"
                log.info(s_p)
                console.log(s_p, style="#FF4689")
                await kb.toggle_setting_button(
                    global_config=self.gc.config, user_config=self.app.config
                )
            except Exception as e:
                await callback_query.message.reply_text(
                    "启用或禁用自动关机失败\n(具体原因请前往终端查看报错信息)"
                )
                log.error(f'启用或禁用自动关机失败,{_t(KeyWord.REASON)}:"{e}"')
        elif callback_data == BotCallbackText.SETTING:
            await kb.toggle_setting_button(
                global_config=self.gc.config, user_config=self.app.config
            )
        elif callback_data == BotCallbackText.EXPORT_TABLE:
            await kb.toggle_table_button(config=self.gc.config)
        elif callback_data == BotCallbackText.DOWNLOAD_SETTING:
            await kb.toggle_download_setting_button(user_config=self.app.config)
        elif callback_data == BotCallbackText.UPLOAD_SETTING:
            await kb.toggle_upload_setting_button(global_config=self.gc.config)
        elif callback_data == BotCallbackText.FORWARD_SETTING:
            await kb.toggle_forward_setting_button(global_config=self.gc.config)
        elif callback_data in (BotCallbackText.LINK_TABLE, BotCallbackText.COUNT_TABLE):
            _prompt_string: str = ""
            _false_text: str = ""
            _choice: str = ""
            res: Union[bool, None] = None
            if callback_data == BotCallbackText.LINK_TABLE:
                _prompt_string: str = "链接统计表"
                _false_text: str = "😵😵😵没有链接需要统计。"
                _choice: str = BotCallbackText.EXPORT_LINK_TABLE
                res: Union[bool, None] = self.app.print_link_table(
                    DownloadTask.LINK_INFO
                )
            elif callback_data == BotCallbackText.COUNT_TABLE:
                _prompt_string: str = "计数统计表"
                _false_text: str = "😵😵😵当前没有任何下载。"
                _choice: str = BotCallbackText.EXPORT_COUNT_TABLE
                res: Union[bool, None] = self.app.print_count_table()
            if res:
                await callback_query.message.edit_text(
                    f"👌👌👌`{_prompt_string}`已发送至您的「终端」请注意查收。"
                )
                await kb.choice_export_table_button(choice=_choice)
                return None
            elif res is False:
                await callback_query.message.edit_text(_false_text)
            else:
                await callback_query.message.edit_text(
                    f"😵‍💫😵‍💫😵‍💫`{_prompt_string}`打印失败。\n(具体原因请前往终端查看报错信息)"
                )
            await kb.back_table_button()
        elif callback_data in (
            BotCallbackText.TOGGLE_LINK_TABLE,
            BotCallbackText.TOGGLE_COUNT_TABLE,
        ):

            async def _toggle_button(_table_type):
                export_config: dict = self.gc.config.get("export_table")
                export_config[_table_type] = not export_config.get(_table_type)
                t_t: str = "链接统计表" if _table_type == "link" else "计数统计表"
                s_t: str = "启用" if export_config.get(_table_type) else "禁用"
                t_p: str = f"退出后导出{t_t}已{s_t}。"
                console.log(t_p, style="#FF4689")
                log.info(t_p)
                self.gc.save_config(self.gc.config)
                await kb.toggle_table_button(config=self.gc.config, choice=_table_type)

            if callback_data == BotCallbackText.TOGGLE_LINK_TABLE:
                await _toggle_button("link")
            elif callback_data == BotCallbackText.TOGGLE_COUNT_TABLE:
                await _toggle_button("count")
        elif callback_data in (
            BotCallbackText.EXPORT_LINK_TABLE,
            BotCallbackText.EXPORT_COUNT_TABLE,
        ):
            _prompt_string: str = ""
            res: Union[bool, None] = False
            if callback_data == BotCallbackText.EXPORT_LINK_TABLE:
                _prompt_string: str = "链接统计表"
                res: Union[bool, None] = self.app.print_link_table(
                    link_info=DownloadTask.LINK_INFO, export=True, only_export=True
                )
            elif callback_data == BotCallbackText.EXPORT_COUNT_TABLE:
                _prompt_string: str = "计数统计表"
                res: Union[bool, None] = self.app.print_count_table(
                    export=True, only_export=True
                )
            if res:
                await callback_query.message.edit_text(
                    f"✅✅✅`{_prompt_string}`已发送至您的「终端」并已「导出」为表格请注意查收。\n(请查看软件目录下`DownloadRecordForm`文件夹)"
                )
            elif res is False:
                await callback_query.message.edit_text("😵😵😵没有链接需要统计。")
            else:
                await callback_query.message.edit_text(
                    f"😵‍💫😵‍💫😵‍💫`{_prompt_string}`导出失败。\n(具体原因请前往终端查看报错信息)"
                )
            await kb.back_table_button()
        elif callback_data in (
            BotCallbackText.UPLOAD_DOWNLOAD,
            BotCallbackText.UPLOAD_DOWNLOAD_DELETE,
        ):

            def _toggle_button(_param: str):
                param: bool = self.gc.get_nesting_config(
                    default_nesting=self.gc.default_upload_nesting,
                    param="upload",
                    nesting_param=_param,
                )
                self.gc.config.get("upload", self.gc.default_upload_nesting)[
                    _param
                ] = not param
                u_s: str = "禁用" if param else "开启"
                u_p: str = ""
                if _param == "delete":
                    u_p: str = (
                        f'遇到"受限转发"时,下载后上传并"删除上传完成的本地文件"的行为已{u_s}。'
                    )
                elif _param == "download_upload":
                    u_p: str = f'遇到"受限转发"时,下载后上传已{u_s}。'
                console.log(u_p, style="#FF4689")
                log.info(u_p)

            try:
                if callback_data == BotCallbackText.UPLOAD_DOWNLOAD:
                    _toggle_button("download_upload")
                elif callback_data == BotCallbackText.UPLOAD_DOWNLOAD_DELETE:
                    _toggle_button("delete")
                self.gc.save_config(self.gc.config)
                await kb.toggle_upload_setting_button(global_config=self.gc.config)
            except Exception as e:
                await callback_query.message.reply_text(
                    "上传设置失败\n(具体原因请前往终端查看报错信息)"
                )
                log.error(f'上传设置失败,{_t(KeyWord.REASON)}:"{e}"')
        elif callback_data in (
            BotCallbackText.TOGGLE_DOWNLOAD_VIDEO,
            BotCallbackText.TOGGLE_DOWNLOAD_PHOTO,
            BotCallbackText.TOGGLE_DOWNLOAD_AUDIO,
            BotCallbackText.TOGGLE_DOWNLOAD_VOICE,
            BotCallbackText.TOGGLE_DOWNLOAD_ANIMATION,
            BotCallbackText.TOGGLE_DOWNLOAD_DOCUMENT,
        ):

            def _toggle_download_type_button(_param: str):
                if _param in self.app.download_type:
                    if len(self.app.download_type) == 1:
                        raise ValueError
                    f_s = "禁用"
                    self.app.download_type.remove(_param)
                else:
                    f_s = "启用"
                    self.app.download_type.append(_param)

                f_p = f'已{f_s}"{_param}"类型的下载。'
                console.log(f_p, style="#FF4689")
                log.info(f_p)

            try:
                if callback_data == BotCallbackText.TOGGLE_DOWNLOAD_VIDEO:
                    _toggle_download_type_button("video")
                elif callback_data == BotCallbackText.TOGGLE_DOWNLOAD_PHOTO:
                    _toggle_download_type_button("photo")
                elif callback_data == BotCallbackText.TOGGLE_DOWNLOAD_AUDIO:
                    _toggle_download_type_button("audio")
                elif callback_data == BotCallbackText.TOGGLE_DOWNLOAD_VOICE:
                    _toggle_download_type_button("voice")
                elif callback_data == BotCallbackText.TOGGLE_DOWNLOAD_ANIMATION:
                    _toggle_download_type_button("animation")
                elif callback_data == BotCallbackText.TOGGLE_DOWNLOAD_DOCUMENT:
                    _toggle_download_type_button("document")
                self.app.config["download_type"] = self.app.download_type
                self.app.save_config(self.app.config)
                await kb.toggle_download_setting_button(self.app.config)
            except ValueError:
                await callback_query.message.reply_text(
                    "⚠️⚠️⚠️至少需要选择一个下载类型⚠️⚠️⚠️"
                )
            except Exception as e:
                await callback_query.message.reply_text(
                    "下载类型设置失败\n(具体原因请前往终端查看报错信息)"
                )
                log.error(f'下载类型设置失败,{_t(KeyWord.REASON)}:"{e}"')
        elif callback_data in (
            BotCallbackText.TOGGLE_FORWARD_VIDEO,
            BotCallbackText.TOGGLE_FORWARD_PHOTO,
            BotCallbackText.TOGGLE_FORWARD_AUDIO,
            BotCallbackText.TOGGLE_FORWARD_VOICE,
            BotCallbackText.TOGGLE_FORWARD_ANIMATION,
            BotCallbackText.TOGGLE_FORWARD_DOCUMENT,
            BotCallbackText.TOGGLE_FORWARD_TEXT,
        ):

            def _toggle_forward_type_button(_param: str):
                _forward_type: dict = self.gc.config.get(
                    "forward_type", self.gc.default_forward_type_nesting
                )
                _status: bool = self.gc.get_nesting_config(
                    default_nesting=self.gc.default_forward_type_nesting,
                    param="forward_type",
                    nesting_param=_param,
                )
                if list(_forward_type.values()).count(True) == 1 and _status:
                    raise ValueError
                _forward_type[_param] = not _status
                f_s = "禁用" if _status else "启用"
                f_p = f'已{f_s}"{_param}"类型的转发。'
                console.log(f_p, style="#FF4689")
                log.info(f_p)

            try:
                if callback_data == BotCallbackText.TOGGLE_FORWARD_VIDEO:
                    _toggle_forward_type_button("video")
                elif callback_data == BotCallbackText.TOGGLE_FORWARD_PHOTO:
                    _toggle_forward_type_button("photo")
                elif callback_data == BotCallbackText.TOGGLE_FORWARD_AUDIO:
                    _toggle_forward_type_button("audio")
                elif callback_data == BotCallbackText.TOGGLE_FORWARD_VOICE:
                    _toggle_forward_type_button("voice")
                elif callback_data == BotCallbackText.TOGGLE_FORWARD_ANIMATION:
                    _toggle_forward_type_button("animation")
                elif callback_data == BotCallbackText.TOGGLE_FORWARD_DOCUMENT:
                    _toggle_forward_type_button("document")
                elif callback_data == BotCallbackText.TOGGLE_FORWARD_TEXT:
                    _toggle_forward_type_button("text")
                self.gc.save_config(self.gc.config)
                await kb.toggle_forward_setting_button(self.gc.config)
            except ValueError:
                await callback_query.message.reply_text(
                    "⚠️⚠️⚠️至少需要选择一个转发类型⚠️⚠️⚠️"
                )
            except Exception as e:
                await callback_query.message.reply_text(
                    "转发设置失败\n(具体原因请前往终端查看报错信息)"
                )
                log.error(f'转发设置失败,{_t(KeyWord.REASON)}:"{e}"')
        elif (
            callback_data == BotCallbackText.REMOVE_LISTEN_FORWARD
            or callback_data.startswith(BotCallbackText.REMOVE_LISTEN_DOWNLOAD)
        ):
            if callback_data.startswith(BotCallbackText.REMOVE_LISTEN_DOWNLOAD):
                args: list = callback_data.split()
                link: str = args[1]
                self.app.client.remove_handler(self.listen_download_chat.get(link))
                self.listen_download_chat.pop(link)
                await callback_query.message.edit_text(link)
                await callback_query.message.edit_reply_markup(
                    KeyboardButton.single_button(
                        text=BotButton.ALREADY_REMOVE,
                        callback_data=BotCallbackText.NULL,
                    )
                )
                p = f'已删除监听下载,频道链接:"{link}"。'
                console.log(p, style="#FF4689")
                log.info(f"{p}当前的监听下载信息:{self.listen_download_chat}")
                return None
            if not isinstance(self.cd.data, dict):
                return None
            meta: Union[dict, None] = self.cd.data.copy()
            self.cd.data = None
            link: str = meta.get("link")
            self.app.client.remove_handler(self.listen_forward_chat.get(link))
            self.listen_forward_chat.pop(link)
            m: list = link.split()
            _ = " -> ".join(m)
            p = f'已删除监听转发,转发规则:"{_}"。'
            await callback_query.message.edit_text(" ➡️ ".join(m))
            await callback_query.message.edit_reply_markup(
                KeyboardButton.single_button(
                    text=BotButton.ALREADY_REMOVE, callback_data=BotCallbackText.NULL
                )
            )
            console.log(p, style="#FF4689")
            log.info(f"{p}当前的监听转发信息:{self.listen_forward_chat}")
        elif callback_data in (
            BotCallbackText.DOWNLOAD_CHAT_FILTER,  # 主页面。
            BotCallbackText.DOWNLOAD_CHAT_DATE_FILTER,  # 下载日期范围设置页面。
            BotCallbackText.DOWNLOAD_CHAT_DTYPE_FILTER,  # 下载类型设置页面。
            BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_VIDEO,
            BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_PHOTO,
            BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_AUDIO,
            BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_VOICE,
            BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_ANIMATION,
            BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_DOCUMENT,
            BotCallbackText.DOWNLOAD_CHAT_ID,  # 执行任务。
            BotCallbackText.DOWNLOAD_CHAT_ID_CANCEL,  # 取消任务。
            BotCallbackText.FILTER_START_DATE,  # 设置下载起始日期。
            BotCallbackText.FILTER_END_DATE,  # 设置下载结束日期。
        ) or callback_data.startswith(
            (
                "time_inc_",
                "time_dec_",
                "set_time_",
                "set_specific_time_",
                "adjust_step_",
            )  # 切换月份,选择日期。
        ):
            chat_id = BotCallbackText.DOWNLOAD_CHAT_ID

            def _get_update_time():
                _start_timestamp = self.download_chat_filter[chat_id]["date_range"][
                    "start_date"
                ]
                _end_timestamp = self.download_chat_filter[chat_id]["date_range"][
                    "end_date"
                ]
                _start_time = (
                    datetime.datetime.fromtimestamp(_start_timestamp)
                    if _start_timestamp
                    else "未定义"
                )
                _end_time = (
                    datetime.datetime.fromtimestamp(_end_timestamp)
                    if _end_timestamp
                    else "未定义"
                )
                return _start_time, _end_time

            def _get_format_dtype():
                _download_type = []
                for _dtype, _status in self.download_chat_filter[chat_id][
                    "download_type"
                ].items():
                    if _status:
                        _download_type.append(_t(_dtype))
                return ",".join(_download_type)

            def _remove_chat_id(_chat_id):
                if _chat_id in self.download_chat_filter:
                    self.download_chat_filter.pop(_chat_id)
                    log.info(f'"{_chat_id}"已从{self.download_chat_filter}中移除。')

            def _filter_prompt():
                return f"💬下载频道:`{chat_id}`\n⏮️当前选择的起始日期为:{_get_update_time()[0]}\n⏭️当前选择的结束日期为:{_get_update_time()[1]}\n📝当前选择的下载类型为:{_get_format_dtype()}"

            async def _verification_time(_start_time, _end_time) -> bool:
                if isinstance(_start_time, datetime.datetime) and isinstance(
                    _end_time, datetime.datetime
                ):
                    if _start_time > _end_time:
                        await callback_query.message.reply_text(
                            text=f"❌❌❌日期设置失败❌❌❌\n"
                            f"`起始日期({_start_time})`>`结束日期({_end_time})`\n"
                        )
                        return False
                    elif _start_time == _end_time:
                        await callback_query.message.reply_text(
                            text=f"❌❌❌日期设置失败❌❌❌\n"
                            f"`起始日期({_start_time})`=`结束日期({_end_time})`\n"
                        )
                        return False
                return True

            if callback_data in (
                BotCallbackText.DOWNLOAD_CHAT_ID,
                BotCallbackText.DOWNLOAD_CHAT_ID_CANCEL,
            ):  # 执行或取消任务。
                BotCallbackText.DOWNLOAD_CHAT_ID = "download_chat_id"
                if callback_data == chat_id:
                    await callback_query.message.edit_text(
                        text=f"下载频道:`{chat_id}`\n{callback_query.message.text}",
                        reply_markup=kb.single_button(
                            text=BotButton.TASK_ASSIGN,
                            callback_data=BotCallbackText.NULL,
                        ),
                    )
                    await self.download_chat(chat_id=chat_id)
                    _remove_chat_id(chat_id)
                elif callback_data == BotCallbackText.DOWNLOAD_CHAT_ID_CANCEL:
                    _remove_chat_id(chat_id)
                    await callback_query.message.edit_text(
                        text=callback_query.message.text,
                        reply_markup=kb.single_button(
                            text=BotButton.TASK_CANCEL,
                            callback_data=BotCallbackText.NULL,
                        ),
                    )
            elif callback_data in (
                BotCallbackText.DOWNLOAD_CHAT_FILTER,
                BotCallbackText.DOWNLOAD_CHAT_DATE_FILTER,
            ):
                if callback_data == BotCallbackText.DOWNLOAD_CHAT_DATE_FILTER:
                    start_time, end_time = _get_update_time()
                    if not await _verification_time(start_time, end_time):
                        return None
                # 返回或点击。
                await callback_query.message.edit_text(
                    text=_filter_prompt(),
                    reply_markup=(
                        kb.download_chat_filter_button()
                        if callback_data == BotCallbackText.DOWNLOAD_CHAT_FILTER
                        else kb.filter_date_range_button()
                    ),
                )
            elif callback_data in (
                BotCallbackText.FILTER_START_DATE,
                BotCallbackText.FILTER_END_DATE,
            ):
                dtype = None
                p_s_d = ""
                if callback_data == BotCallbackText.FILTER_START_DATE:
                    dtype = CalenderKeyboard.START_TIME_BUTTON
                    p_s_d = "起始"
                elif callback_data == BotCallbackText.FILTER_END_DATE:
                    dtype = CalenderKeyboard.END_TIME_BUTTON
                    p_s_d = "结束"
                await callback_query.message.edit_text(
                    text=f"📅选择{p_s_d}日期:\n{_filter_prompt()}"
                )
                await kb.calendar_keyboard(dtype=dtype)
            elif callback_data.startswith("adjust_step_"):
                # 获取当前步进值
                parts = callback_data.split("_")
                dtype = parts[-2]
                current_step = int(parts[-1])
                step_sequence = [1, 2, 5, 10, 15, 20]
                current_index = step_sequence.index(current_step)
                next_index = (current_index + 1) % len(step_sequence)
                new_step = step_sequence[next_index]
                self.download_chat_filter[chat_id]["date_range"][
                    "adjust_step"
                ] = new_step
                current_date = datetime.datetime.fromtimestamp(
                    self.download_chat_filter[chat_id]["date_range"][f"{dtype}_date"]
                ).strftime("%Y-%m-%d %H:%M:%S")
                await callback_query.message.edit_reply_markup(
                    reply_markup=kb.time_keyboard(
                        dtype=dtype, date=current_date, adjust_step=new_step
                    )
                )
            elif callback_data.startswith(("time_inc_", "time_dec_")):
                parts = callback_data.split("_")
                dtype = None
                if "start" in callback_data:
                    dtype = CalenderKeyboard.START_TIME_BUTTON
                elif "end" in callback_data:
                    dtype = CalenderKeyboard.END_TIME_BUTTON

                if "month" in callback_data:
                    year = int(parts[-2])
                    month = int(parts[-1])
                    await kb.calendar_keyboard(year=year, month=month, dtype=dtype)
                    log.info(f"日期切换为{year}年,{month}月。")

            elif callback_data.startswith(("set_time_", "set_specific_time_")):
                parts = callback_data.split("_")
                date = parts[-1]
                dtype = parts[-2]
                date_type = ""
                p_s_d = ""
                timestamp = datetime.datetime.timestamp(
                    datetime.datetime.strptime(date, "%Y-%m-%d %H:%M:%S")
                )
                if "start" in callback_data:
                    date_type = "start_date"
                    p_s_d = "起始"
                elif "end" in callback_data:
                    date_type = "end_date"
                    p_s_d = "结束"
                self.download_chat_filter[chat_id]["date_range"][date_type] = timestamp
                await callback_query.message.edit_text(
                    text=f"📅选择{p_s_d}日期:\n{_filter_prompt()}",
                    reply_markup=kb.time_keyboard(
                        dtype=dtype,
                        date=date,
                        adjust_step=self.download_chat_filter[chat_id]["date_range"][
                            "adjust_step"
                        ],
                    ),
                )
                log.info(
                    f"日期设置,起始日期:{_get_update_time()[0]},结束日期:{_get_update_time()[1]}。"
                )
            elif callback_data in (
                BotCallbackText.DOWNLOAD_CHAT_DTYPE_FILTER,
                BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_VIDEO,
                BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_PHOTO,
                BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_AUDIO,
                BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_VOICE,
                BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_ANIMATION,
                BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_DOCUMENT,
            ):

                def _toggle_dtype_filter_button(_param: str):
                    _dtype: dict = self.download_chat_filter[chat_id]["download_type"]
                    _status: bool = _dtype[_param]
                    if list(_dtype.values()).count(True) == 1 and _status:
                        raise ValueError
                    _dtype[_param] = not _status
                    f_s = "禁用" if _status else "启用"
                    f_p = f'已{f_s}"{_param}"类型用于/download_chat命令的下载。'
                    log.info(f"{f_p}当前的/download_chat下载类型设置:{_dtype}")

                try:
                    if (
                        callback_data
                        == BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_VIDEO
                    ):
                        _toggle_dtype_filter_button("video")
                    elif (
                        callback_data
                        == BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_PHOTO
                    ):
                        _toggle_dtype_filter_button("photo")
                    elif (
                        callback_data
                        == BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_AUDIO
                    ):
                        _toggle_dtype_filter_button("audio")
                    elif (
                        callback_data
                        == BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_VOICE
                    ):
                        _toggle_dtype_filter_button("voice")
                    elif (
                        callback_data
                        == BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_ANIMATION
                    ):
                        _toggle_dtype_filter_button("animation")
                    elif (
                        callback_data
                        == BotCallbackText.TOGGLE_DOWNLOAD_CHAT_DTYPE_DOCUMENT
                    ):
                        _toggle_dtype_filter_button("document")
                    await callback_query.message.edit_text(
                        text=_filter_prompt(),
                        reply_markup=kb.toggle_download_chat_type_filter_button(
                            self.download_chat_filter
                        ),
                    )
                except ValueError:
                    await callback_query.message.reply_text(
                        "⚠️⚠️⚠️至少需要选择一个下载类型⚠️⚠️⚠️"
                    )
                except Exception as e:
                    await callback_query.message.reply_text(
                        "下载类型设置失败\n(具体原因请前往终端查看报错信息)"
                    )
                    log.error(
                        f'下载类型设置失败,{_t(KeyWord.REASON)}:"{e}"', exc_info=True
                    )

    async def forward(
        self,
        client: pyrogram.Client,
        message: pyrogram.types.Message,
        message_id: int,
        origin_chat_id: int,
        target_chat_id: int,
        target_link: str,
        download_upload: Optional[bool] = False,
        media_group: Optional[list] = None,
    ):
        try:
            if not self.check_type(message):
                console.log(
                    f'{_t(KeyWord.CHANNEL)}:"{target_chat_id}",{_t(KeyWord.MESSAGE_ID)}:"{message_id}"'
                    f" -> "
                    f'{_t(KeyWord.CHANNEL)}:"{origin_chat_id}",'
                    f"{_t(KeyWord.STATUS)}:{_t(KeyWord.FORWARD_SKIP)}。"
                )
                return None
            if media_group:
                await self.app.client.copy_media_group(
                    chat_id=target_chat_id,
                    from_chat_id=origin_chat_id,
                    message_id=message_id,
                    disable_notification=True,
                )
            else:
                await self.app.client.copy_message(
                    chat_id=target_chat_id,
                    from_chat_id=origin_chat_id,
                    message_id=message_id,
                    disable_notification=True,
                    protect_content=False,
                )
            p_message_id = (
                ",".join(map(str, media_group)) if media_group else message_id
            )
            console.log(
                f'{_t(KeyWord.CHANNEL)}:"{target_chat_id}",{_t(KeyWord.MESSAGE_ID)}:"{p_message_id}"'
                f" -> "
                f'{_t(KeyWord.CHANNEL)}:"{origin_chat_id}",'
                f"{_t(KeyWord.STATUS)}:{_t(KeyWord.FORWARD_SUCCESS)}。"
            )
        except (ChatForwardsRestricted_400, ChatForwardsRestricted_406):
            if not download_upload:
                raise
            link = message.link
            if not self.gc.download_upload:
                await self.bot.send_message(
                    chat_id=client.me.id,
                    text=f"⚠️⚠️⚠️无法转发⚠️⚠️⚠️\n"
                    f"`{link}`\n"
                    f"存在内容保护限制(可在[设置]->[上传设置]中设置转发时遇到受限转发进行下载后上传)。",
                    reply_parameters=ReplyParameters(message_id=message_id),
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    BotButton.SETTING,
                                    callback_data=BotCallbackText.SETTING,
                                )
                            ]
                        ]
                    ),
                )
                return None
            self.last_message.text = f"/download {link}?single"
            await self.get_download_link_from_bot(
                client=self.last_client,
                message=self.last_message,
                with_upload={
                    "link": target_link,
                    "file_name": None,
                    "with_delete": self.gc.upload_delete,
                },
            )
            p = f'{_t(KeyWord.DOWNLOAD_AND_UPLOAD_TASK)}{_t(KeyWord.CHANNEL)}:"{target_chat_id}",{_t(KeyWord.LINK)}:"{link}"。'
            console.log(p, style="#FF4689")
            log.info(p)

    async def get_forward_link_from_bot(
        self, client: pyrogram.Client, message: pyrogram.types.Message
    ) -> Union[dict, None]:
        meta: Union[dict, None] = await super().get_forward_link_from_bot(
            client, message
        )
        if meta is None:
            return None
        self.last_client: pyrogram.Client = client
        self.last_message: pyrogram.types.Message = message
        origin_link: str = meta.get("origin_link")
        target_link: str = meta.get("target_link")
        start_id: int = meta.get("message_range")[0]
        end_id: int = meta.get("message_range")[1]
        last_message: Union[pyrogram.types.Message, None] = None
        loading = "🚛消息转发中,请稍候..."
        try:
            origin_meta: Union[dict, None] = await parse_link(
                client=self.app.client, link=origin_link
            )
            target_meta: Union[dict, None] = await parse_link(
                client=self.app.client, link=target_link
            )
            if not all([origin_meta, target_meta]):
                raise Exception("Invalid origin_link or target_link.")
            origin_chat: Union[pyrogram.types.Chat, None] = await get_chat_with_notify(
                user_client=self.app.client,
                bot_client=client,
                bot_message=message,
                chat_id=origin_meta.get("chat_id"),
                error_msg=f"⬇️⬇️⬇️原始频道不存在⬇️⬇️⬇️\n{origin_link}",
            )
            target_chat: Union[pyrogram.types.Chat, None] = await get_chat_with_notify(
                user_client=self.app.client,
                bot_client=client,
                bot_message=message,
                chat_id=target_meta.get("chat_id"),
                error_msg=f"⬇️⬇️⬇️目标频道不存在⬇️⬇️⬇️\n{target_link}",
            )
            if not all([origin_chat, target_chat]):
                return None
            me = await client.get_me()
            if target_chat.id == me.id:
                await client.send_message(
                    chat_id=message.from_user.id,
                    text="⚠️⚠️⚠️无法转发到此机器人⚠️⚠️⚠️",
                    reply_parameters=ReplyParameters(message_id=message.id),
                )
                return None
            origin_chat_id = origin_chat.id
            target_chat_id = target_chat.id
            record_id: list = []
            last_message = await client.send_message(
                chat_id=message.from_user.id,
                reply_parameters=ReplyParameters(message_id=message.id),
                link_preview_options=LINK_PREVIEW_OPTIONS,
                text=loading,
            )
            async for i in self.app.client.get_chat_history(
                chat_id=origin_chat.id, offset_id=start_id, max_id=end_id, reverse=True
            ):
                try:
                    message_id = i.id
                    await self.forward(
                        client=client,
                        message=i,
                        message_id=message_id,
                        origin_chat_id=origin_chat_id,
                        target_chat_id=target_chat_id,
                        target_link=target_link,
                    )
                    record_id.append(message_id)
                except (ChatForwardsRestricted_400, ChatForwardsRestricted_406):
                    self.cd.data = {
                        "origin_link": origin_link,
                        "target_link": target_link,
                        "start_id": start_id,
                        "end_id": end_id,
                    }
                    channel = (
                        "@" + origin_chat.username
                        if isinstance(getattr(origin_chat, "username"), str)
                        else ""
                    )
                    await client.send_message(
                        chat_id=message.from_user.id,
                        text=f"⚠️⚠️⚠️无法转发⚠️⚠️⚠️\n`{origin_link}`\n{channel}存在内容保护限制。",
                        parse_mode=ParseMode.MARKDOWN,
                        reply_parameters=ReplyParameters(message_id=message.id),
                        reply_markup=KeyboardButton.restrict_forward_button(),
                    )
                    return None
                except Exception as e:
                    log.warning(
                        f'{_t(KeyWord.CHANNEL)}:"{origin_chat_id}",{_t(KeyWord.MESSAGE_ID)}:"{i.id}"'
                        f" -> "
                        f'{_t(KeyWord.CHANNEL)}:"{target_chat_id}",'
                        f"{_t(KeyWord.STATUS)}:{_t(KeyWord.FORWARD_FAILURE)},"
                        f'{_t(KeyWord.REASON)}:"{e}"'
                    )
            else:
                if isinstance(last_message, str):
                    log.warning("消息过长编辑频繁,暂时无法通过机器人显示通知。")
                if not record_id:
                    last_message = await self.safe_edit_message(
                        client=client,
                        message=message,
                        last_message_id=last_message.id,
                        text=safe_message(f"😅😅😅没有找到任何有效的消息😅😅😅"),
                    )
                    return None
                invalid_id: list = []
                for i in range(start_id, end_id + 1):
                    if i not in record_id:
                        invalid_id.append(i)
                if invalid_id:
                    last_message = await self.safe_edit_message(
                        client=client,
                        message=message,
                        last_message_id=last_message.id,
                        text=safe_message(BotMessage.INVALID),
                    )
                    for i in invalid_id:
                        last_message: Union[pyrogram.types.Message, str, None] = (
                            await self.safe_edit_message(
                                client=client,
                                message=message,
                                last_message_id=last_message.id,
                                text=safe_message(
                                    f"{last_message.text}\n{format_chat_link(origin_link, topic=origin_chat.is_forum)}/{i}"
                                ),
                            )
                        )
                last_message = await self.safe_edit_message(
                    client=client,
                    message=message,
                    last_message_id=last_message.id,
                    text=safe_message(
                        f"{last_message.text.strip(loading)}\n🌟🌟🌟转发任务已完成🌟🌟🌟\n(若设置了转发过滤规则,请前往终端查看转发记录,此处不做展示)"
                    ),
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    BotButton.CLICK_VIEW,
                                    url=format_chat_link(
                                        target_link, topic=target_chat.is_forum
                                    ),
                                )
                            ]
                        ]
                    ),
                )
        except AttributeError as e:
            log.exception(f'转发时遇到错误,{_t(KeyWord.REASON)}:"{e}"')
            await client.send_message(
                chat_id=message.from_user.id,
                reply_parameters=ReplyParameters(message_id=message.id),
                text="⬇️⬇️⬇️出错了⬇️⬇️⬇️\n(具体原因请前往终端查看报错信息)",
            )
        except (ValueError, KeyError, UsernameInvalid, ChatWriteForbidden):
            msg: str = ""
            if any("/c" in link for link in (origin_link, target_link)):
                msg = "(私密频道或话题频道必须让当前账号加入转发频道,并且目标频道需有上传文件的权限)"
            await client.send_message(
                chat_id=message.from_user.id,
                reply_parameters=ReplyParameters(message_id=message.id),
                text="❌❌❌没有找到有效链接❌❌❌\n" + msg,
            )
        except Exception as e:
            log.exception(f'转发时遇到错误,{_t(KeyWord.REASON)}:"{e}"')
            await client.send_message(
                chat_id=message.from_user.id,
                reply_parameters=ReplyParameters(message_id=message.id),
                text="⬇️⬇️⬇️出错了⬇️⬇️⬇️\n(具体原因请前往终端查看报错信息)",
            )
        finally:
            if last_message and last_message.text == loading:
                await last_message.delete()

    async def cancel_listen(
        self, client: pyrogram.Client, message: pyrogram.types, link: str, command: str
    ):
        if command == "/listen_forward":
            self.cd.data = {"link": link}
        args: list = link.split()
        forward_emoji = " ➡️ "
        await client.send_message(
            chat_id=message.from_user.id,
            reply_parameters=ReplyParameters(message_id=message.id),
            text=f"`{link if len(args) == 1 else forward_emoji.join(args)}`\n⚠️⚠️⚠️已经在监听列表中⚠️⚠️⚠️\n请选择是否移除",
            link_preview_options=LINK_PREVIEW_OPTIONS,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            BotButton.OK,
                            callback_data=(
                                f"{BotCallbackText.REMOVE_LISTEN_DOWNLOAD} {link}"
                                if command == "/listen_download"
                                else BotCallbackText.REMOVE_LISTEN_FORWARD
                            ),
                        ),
                        InlineKeyboardButton(
                            BotButton.CANCEL, callback_data=BotCallbackText.NULL
                        ),
                    ]
                ]
            ),
        )

    async def on_listen(self, client: pyrogram.Client, message: pyrogram.types.Message):
        meta: Union[dict, None] = await super().on_listen(client, message)
        if meta is None:
            return None

        tag: Union[str, None] = meta.get("tag")

        async def add_listen_chat(
            _link: str, _listen_chat: dict, _callback: callable
        ) -> bool:
            if _link not in _listen_chat:
                try:
                    chat = await self.user.get_chat(_link)
                    if chat.is_forum:
                        raise PeerIdInvalid
                    handler = MessageHandler(
                        _callback, filters=pyrogram.filters.chat(chat.id)
                    )
                    _listen_chat[_link] = handler
                    self.user.add_handler(handler)
                    # 记录监听频道的标签
                    try:
                        if tag:
                            self.listen_download_tag_by_chatid[chat.id] = tag
                    except Exception:
                        pass
                    return True
                except PeerIdInvalid:
                    try:
                        link_meta: list = _link.split()
                        link_length: int = len(link_meta)
                        if (
                            link_length >= 1
                        ):  # v1.6.7 修复内部函数add_listen_chat中,抛出PeerIdInvalid后,在获取链接时抛出ValueError错误。
                            l_link = link_meta[0]
                        else:
                            return False
                        m: dict = await parse_link(client=self.app.client, link=l_link)
                        topic_id = m.get("topic_id")
                        chat_id = m.get("chat_id")
                        if topic_id:
                            filters = pyrogram.filters.chat(
                                chat_id
                            ) & pyrogram.filters.topic(topic_id)
                        else:
                            filters = pyrogram.filters.chat(chat_id)
                        handler = MessageHandler(_callback, filters=filters)
                        _listen_chat[_link] = handler
                        self.user.add_handler(handler)
                        # 记录监听频道的标签
                        try:
                            if tag and chat_id is not None:
                                self.listen_download_tag_by_chatid[chat_id] = tag
                        except Exception:
                            pass
                        return True
                    except ValueError as e:
                        await client.send_message(
                            chat_id=message.from_user.id,
                            reply_parameters=ReplyParameters(message_id=message.id),
                            link_preview_options=LINK_PREVIEW_OPTIONS,
                            text=f"⚠️⚠️⚠️无法读取⚠️⚠️⚠️\n`{_link}`\n(具体原因请前往终端查看报错信息)",
                        )
                        log.error(f'频道"{_link}"解析失败,{_t(KeyWord.REASON)}:"{e}"')
                        return False
                except Exception as e:
                    await client.send_message(
                        chat_id=message.from_user.id,
                        reply_parameters=ReplyParameters(message_id=message.id),
                        link_preview_options=LINK_PREVIEW_OPTIONS,
                        text=f"⚠️⚠️⚠️无法读取⚠️⚠️⚠️\n`{_link}`\n(具体原因请前往终端查看报错信息)",
                    )
                    log.error(f'读取频道"{_link}"时遇到错误,{_t(KeyWord.REASON)}:"{e}"')
                    return False
            else:
                await self.cancel_listen(client, message, _link, command)
                return False

        links: list = meta.get("links")
        command: str = meta.get("command")
        if command == "/listen_download":
            last_message: Union[pyrogram.types.Message, None] = None
            for link in links:
                if await add_listen_chat(
                    link, self.listen_download_chat, self.listen_download
                ):
                    if not last_message:
                        last_message: Union[pyrogram.types.Message, str, None] = (
                            await client.send_message(
                                chat_id=message.from_user.id,
                                reply_parameters=ReplyParameters(message_id=message.id),
                                link_preview_options=LINK_PREVIEW_OPTIONS,
                                text=f"✅新增`监听下载频道`频道:\n",
                            )
                        )
                    last_message: Union[pyrogram.types.Message, str, None] = (
                        await self.safe_edit_message(
                            client=client,
                            message=message,
                            last_message_id=last_message.id,
                            text=safe_message(f"{last_message.text}\n{link}"),
                            reply_markup=InlineKeyboardMarkup(
                                [
                                    [
                                        InlineKeyboardButton(
                                            BotButton.LOOKUP_LISTEN_INFO,
                                            callback_data=BotCallbackText.LOOKUP_LISTEN_INFO,
                                        )
                                    ]
                                ]
                            ),
                        )
                    )
                    p = f'已新增监听下载,频道链接:"{link}"。'
                    console.log(p, style="#FF4689")
                    log.info(f"{p}当前的监听下载信息:{self.listen_download_chat}")
        elif command == "/listen_forward":
            listen_link, target_link = links
            if await add_listen_chat(
                f"{listen_link} {target_link}",
                self.listen_forward_chat,
                self.listen_forward,
            ):
                await client.send_message(
                    chat_id=message.from_user.id,
                    reply_parameters=ReplyParameters(message_id=message.id),
                    link_preview_options=LINK_PREVIEW_OPTIONS,
                    text=f"✅新增`监听转发`频道:\n{listen_link} ➡️ {target_link}",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    BotButton.LOOKUP_LISTEN_INFO,
                                    callback_data=BotCallbackText.LOOKUP_LISTEN_INFO,
                                )
                            ]
                        ]
                    ),
                )
                p = f'已新增监听转发,转发规则:"{listen_link} -> {target_link}"。'
                console.log(p, style="#FF4689")
                log.info(f"{p}当前的监听转发信息:{self.listen_forward_chat}")

    async def listen_download(
        self, client: pyrogram.Client, message: pyrogram.types.Message
    ):
        try:
            # 若该监听频道设置了标签, 为当前消息链接记录标签
            try:
                _chat_id = getattr(getattr(message, "chat", None), "id", None)
                _tag = self.listen_download_tag_by_chatid.get(_chat_id)
                if _tag and getattr(message, "link", None):
                    self.link_tag_map[message.link] = _tag
            except Exception:
                pass
            await self.create_download_task(message_ids=message.link, single_link=True)
        except Exception as e:
            log.exception(f"监听下载出现错误,{_t(KeyWord.REASON)}:{e}")

    def check_type(self, message: pyrogram.types.Message):
        for dtype, is_forward in self.gc.forward_type.items():
            if is_forward:
                result = getattr(message, dtype)
                if result:
                    return True
        return False

    async def listen_forward(
        self, client: pyrogram.Client, message: pyrogram.types.Message
    ):
        try:
            link: str = message.link
            meta = await parse_link(client=self.app.client, link=link)
            listen_chat_id = meta.get("chat_id")
            for m in self.listen_forward_chat:
                listen_link, target_link = m.split()
                _listen_link_meta = await parse_link(
                    client=self.app.client, link=listen_link
                )
                _target_link_meta = await parse_link(
                    client=self.app.client, link=target_link
                )
                _listen_chat_id = _listen_link_meta.get("chat_id")
                _target_chat_id = _target_link_meta.get("chat_id")
                if listen_chat_id == _listen_chat_id:
                    try:
                        media_group_ids = await message.get_media_group()
                        if not media_group_ids:
                            raise ValueError
                        if not self.gc.forward_type.get(
                            "video"
                        ) or not self.gc.forward_type.get("photo"):
                            log.warning(
                                "由于过滤了图片或视频类型的转发,将不再以媒体组方式发送。"
                            )
                            raise ValueError
                        if (
                            getattr(getattr(message, "chat", None), "is_creator", False)
                            or getattr(
                                getattr(message, "chat", None), "is_admin", False
                            )
                        ) and (
                            getattr(getattr(message, "from_user", None), "id", -1)
                            == getattr(getattr(client, "me", None), "id", None)
                        ):
                            pass
                        elif (
                            getattr(
                                getattr(message, "chat", None),
                                "has_protected_content",
                                False,
                            )
                            or getattr(
                                getattr(message, "sender_chat", None),
                                "has_protected_content",
                                False,
                            )
                            or getattr(message, "has_protected_content", False)
                        ):
                            raise ValueError
                        if not self.handle_media_groups.get(listen_chat_id):
                            self.handle_media_groups[listen_chat_id] = set()
                        if (
                            listen_chat_id in self.handle_media_groups
                            and message.id
                            not in self.handle_media_groups.get(listen_chat_id)
                        ):
                            ids: set = set()
                            for peer_message in media_group_ids:
                                peer_id = peer_message.id
                                ids.add(peer_id)
                            if ids:
                                old_ids: Union[None, set] = (
                                    self.handle_media_groups.get(listen_chat_id)
                                )
                                if old_ids and isinstance(old_ids, set):
                                    old_ids.update(ids)
                                    self.handle_media_groups[listen_chat_id] = old_ids
                                else:
                                    self.handle_media_groups[listen_chat_id] = ids
                            await self.forward(
                                client=client,
                                message=message,
                                message_id=message.id,
                                origin_chat_id=_listen_chat_id,
                                target_chat_id=_target_chat_id,
                                target_link=target_link,
                                download_upload=False,
                                media_group=sorted(ids),
                            )
                            break
                        break
                    except ValueError:
                        pass
                    await self.forward(
                        client=client,
                        message=message,
                        message_id=message.id,
                        origin_chat_id=_listen_chat_id,
                        target_chat_id=_target_chat_id,
                        target_link=target_link,
                        download_upload=True,
                    )
        except (ValueError, KeyError, UsernameInvalid, ChatWriteForbidden) as e:
            log.error(
                f"监听转发出现错误,{_t(KeyWord.REASON)}:{e}频道性质可能发生改变,包括但不限于(频道解散、频道名改变、频道类型改变、该账户没有在目标频道上传的权限、该账号被当前频道移除)。"
            )
        except Exception as e:
            log.exception(f"监听转发出现错误,{_t(KeyWord.REASON)}:{e}")

    def _get_progress_tracker(self, chat_id: int) -> Optional[TelegramProgressTracker]:
        """获取或创建指定聊天的进度追踪器."""
        if chat_id not in self.telegram_progress_trackers:
            try:
                self.telegram_progress_trackers[chat_id] = TelegramProgressTracker(
                    client=self.bot if self.bot else self.app.client,
                    chat_id=chat_id,
                    update_interval=2.0,
                )
            except Exception as e:
                log.warning(f"创建进度追踪器失败: {e}")
                return None
        return self.telegram_progress_trackers.get(chat_id)

    async def resume_download(
        self,
        message: Union[pyrogram.types.Message, str],
        file_name: str,
        progress: Callable = None,
        progress_args: tuple = (),
        chunk_size: int = 1024 * 1024,
        compare_size: Union[
            int, None
        ] = None,  # 不为None时,将通过大小比对判断是否为完整文件。
        telegram_progress_task_id: Optional[str] = None,  # Telegram 进度任务 ID
        telegram_chat_id: Optional[int] = None,  # Telegram 聊天 ID
    ) -> str:
        temp_path = f"{file_name}.temp"
        if os.path.exists(file_name) and compare_size:
            local_file_size: int = get_file_size(file_path=file_name)
            if compare_file_size(a_size=local_file_size, b_size=compare_size):
                console.log(
                    f"{_t(KeyWord.DOWNLOAD_TASK)}"
                    f'{_t(KeyWord.RESUME)}:"{file_name}",'
                    f"{_t(KeyWord.STATUS)}:{_t(KeyWord.ALREADY_EXIST)}"
                )
                return file_name
            else:
                result: str = safe_replace(
                    origin_file=file_name, overwrite_file=temp_path
                ).get("e_code")
                log.warning(result) if result is not None else None
                log.warning(
                    f'不完整的文件"{file_name}",'
                    f"更改文件名作为缓存:[{file_name}]({get_file_size(file_name)}) -> [{temp_path}]({compare_size})。"
                )
        if os.path.exists(temp_path) and compare_size:
            local_file_size: int = get_file_size(file_path=temp_path)
            if compare_file_size(a_size=local_file_size, b_size=compare_size):
                console.log(
                    f"{_t(KeyWord.DOWNLOAD_TASK)}"
                    f'{_t(KeyWord.RESUME)}:"{temp_path}",'
                    f"{_t(KeyWord.STATUS)}:{_t(KeyWord.ALREADY_EXIST)}"
                )
                result: str = safe_replace(
                    origin_file=temp_path, overwrite_file=file_name
                ).get("e_code")
                log.warning(result) if result is not None else None
                return file_name
            elif local_file_size > compare_size:
                safe_delete(temp_path)
                log.warning(
                    f'错误的缓存文件"{temp_path}",'
                    f"已清除({_t(KeyWord.ERROR_SIZE)}:{local_file_size} > {_t(KeyWord.ACTUAL_SIZE)}:{compare_size})。"
                )
        downloaded = (
            os.path.getsize(temp_path) if os.path.exists(temp_path) else 0
        )  # 获取已下载的字节数。
        if downloaded == 0:
            mode = "wb"
        else:
            mode = "ab"
            console.log(
                f"{_t(KeyWord.DOWNLOAD_TASK)}"
                f'{_t(KeyWord.RESUME)}:"{file_name}",'
                f"{_t(KeyWord.ERROR_SIZE)}:{MetaData.suitable_units_display(downloaded)}。"
            )
        with open(file=temp_path, mode=mode) as f:
            skip_chunks: int = downloaded // chunk_size  # 计算要跳过的块数。
            async for chunk in self.app.client.stream_media(
                message=message, offset=skip_chunks
            ):
                f.write(chunk)
                downloaded += len(chunk)
                # 更新终端进度条
                progress(downloaded, *progress_args)
                # 更新 Telegram 进度（如果启用）
                if telegram_progress_task_id and telegram_chat_id:
                    tracker = self._get_progress_tracker(telegram_chat_id)
                    if tracker and compare_size:
                        # 从文件名中提取显示名称
                        display_name = os.path.basename(file_name)
                        await tracker.update_progress(
                            telegram_progress_task_id,
                            display_name,
                            downloaded,
                            compare_size,
                        )
        if compare_size is None or compare_file_size(
            a_size=downloaded, b_size=compare_size
        ):
            result: str = safe_replace(
                origin_file=temp_path, overwrite_file=file_name
            ).get("e_code")
            log.warning(result) if result is not None else None
            log.info(
                f'"{temp_path}"下载完成,更改文件名:[{temp_path}]({get_file_size(temp_path)}) -> [{file_name}]({compare_size})'
            )
        return file_name

    def get_media_meta(
        self, message: pyrogram.types.Message, dtype
    ) -> Dict[str, Union[int, str]]:
        """获取媒体元数据。"""
        file_id: int = getattr(message, "id")
        temp_file_path: str = self.app.get_temp_file_path(message, dtype)
        _sever_meta = getattr(message, dtype)
        sever_file_size: int = getattr(_sever_meta, "file_size")
        file_name: str = split_path(temp_file_path).get("file_name")
        save_directory: str = os.path.join(self.env_save_directory(message), file_name)
        format_file_size: str = MetaData.suitable_units_display(sever_file_size)
        return {
            "file_id": file_id,
            "temp_file_path": temp_file_path,
            "sever_file_size": sever_file_size,
            "file_name": file_name,
            "save_directory": save_directory,
            "format_file_size": format_file_size,
        }

    async def __add_task(
        self,
        chat_id: Union[str, int],
        link_type: str,
        link: str,
        message: Union[pyrogram.types.Message, list],
        retry: dict,
        with_upload: Union[dict, None] = None,
        diy_download_type: Optional[list] = None,
    ) -> None:
        retry_count = retry.get("count")
        retry_id = retry.get("id")
        if isinstance(message, list):
            for _message in message:
                if retry_count != 0:
                    if _message.id == retry_id:
                        await self.__add_task(
                            chat_id,
                            link_type,
                            link,
                            _message,
                            retry,
                            with_upload,
                            diy_download_type,
                        )
                        break
                else:
                    await self.__add_task(
                        chat_id,
                        link_type,
                        link,
                        _message,
                        retry,
                        with_upload,
                        diy_download_type,
                    )
        else:
            _task = None
            valid_dtype: str = next(
                (_ for _ in DownloadType() if getattr(message, _, None)), None
            )  # 判断该链接是否为有支持的类型。
            download_type: list = (
                diy_download_type if diy_download_type else self.app.download_type
            )
            if valid_dtype in download_type:
                # 如果是匹配到的消息类型就创建任务。
                console.log(
                    f"{_t(KeyWord.DOWNLOAD_TASK)}"
                    f'{_t(KeyWord.CHANNEL)}:"{chat_id}",'  # 频道名。
                    f'{_t(KeyWord.LINK)}:"{link}",'  # 链接。
                    f"{_t(KeyWord.LINK_TYPE)}:{_t(link_type)}。"  # 链接类型。
                )
                while (
                    self.app.current_task_num >= self.app.max_download_task
                ):  # v1.0.7 增加下载任务数限制。
                    await self.event.wait()
                    self.event.clear()
                # 在获取元数据前建立消息与标签的映射
                try:
                    _chat_id = getattr(getattr(message, "chat", None), "id", None)
                    _mid = getattr(message, "id", None)
                    _tag = self.link_tag_map.get(link)
                    if not _tag and _chat_id is not None:
                        _tag = self.listen_download_tag_by_chatid.get(_chat_id)
                    if _tag and _chat_id is not None and _mid is not None:
                        self.message_tag_map[(_chat_id, _mid)] = _tag
                except Exception:
                    pass
                (
                    file_id,
                    temp_file_path,
                    sever_file_size,
                    file_name,
                    save_directory,
                    format_file_size,
                ) = self.get_media_meta(message=message, dtype=valid_dtype).values()
                retry["id"] = file_id
                if is_file_duplicate(
                    save_directory=save_directory, sever_file_size=sever_file_size
                ):  # 检测是否存在。
                    self.download_complete_callback(
                        sever_file_size=sever_file_size,
                        temp_file_path=temp_file_path,
                        link=link,
                        message=message,
                        file_name=file_name,
                        retry_count=retry_count,
                        file_id=file_id,
                        format_file_size=format_file_size,
                        task_id=None,
                        with_upload=with_upload,
                        diy_download_type=diy_download_type,
                        _future=save_directory,
                    )
                else:
                    # 准备 Telegram 进度追踪
                    telegram_task_id = None
                    telegram_chat_id = None
                    try:
                        if isinstance(message, pyrogram.types.Message):
                            from_user = getattr(message, 'from_user', None)
                            if from_user:
                                telegram_chat_id = getattr(from_user, 'id', None)
                                if telegram_chat_id:
                                    tracker = self._get_progress_tracker(telegram_chat_id)
                                    if tracker:
                                        telegram_task_id = f"{file_id}_{int(time.time())}"
                                        await tracker.create_progress_message(
                                            telegram_task_id, file_name
                                        )
                    except Exception as e:
                        log.debug(f"创建 Telegram 进度消息失败: {e}")
                    
                    console.log(
                        f"{_t(KeyWord.DOWNLOAD_TASK)}"
                        f'{_t(KeyWord.FILE)}:"{file_name}",'
                        f"{_t(KeyWord.SIZE)}:{format_file_size},"
                        f"{_t(KeyWord.TYPE)}:{_t(self.app.get_file_type(message, file_name, DownloadStatus.DOWNLOADING))},"
                        f"{_t(KeyWord.STATUS)}:{_t(DownloadStatus.DOWNLOADING)}。"
                    )
                    task_id = self.pb.progress.add_task(
                        description="📥",
                        filename=truncate_display_filename(file_name),
                        info=f"0.00B/{format_file_size}",
                        total=sever_file_size,
                    )
                    _task = self.loop.create_task(
                        self.resume_download(
                            message=message,
                            file_name=temp_file_path,
                            progress=self.pb.bar,
                            progress_args=(sever_file_size, self.pb.progress, task_id),
                            compare_size=sever_file_size,
                            telegram_progress_task_id=telegram_task_id,
                            telegram_chat_id=telegram_chat_id,
                        )
                    )
                    MetaData.print_current_task_num(
                        prompt=_t(KeyWord.CURRENT_DOWNLOAD_TASK),
                        num=self.app.current_task_num,
                    )
                    _task.add_done_callback(
                        partial(
                            self.download_complete_callback,
                            sever_file_size,
                            temp_file_path,
                            link,
                            message,
                            file_name,
                            retry_count,
                            file_id,
                            format_file_size,
                            task_id,
                            with_upload,
                            diy_download_type,
                            telegram_task_id,
                            telegram_chat_id,
                        )
                    )
            else:
                _error = "不支持或被忽略的类型(已取消)。"
                try:
                    _, __, ___, file_name, ____, format_file_size = self.get_media_meta(
                        message=message, dtype=valid_dtype
                    ).values()
                    if file_name:
                        console.log(
                            f"{_t(KeyWord.DOWNLOAD_TASK)}"
                            f'{_t(KeyWord.FILE)}:"{file_name}",'
                            f"{_t(KeyWord.SIZE)}:{format_file_size},"
                            f"{_t(KeyWord.TYPE)}:{_t(self.app.get_file_type(message, file_name, DownloadStatus.SKIP))},"
                            f"{_t(KeyWord.STATUS)}:{_t(DownloadStatus.SKIP)}。"
                        )
                        DownloadTask.set_error(
                            link=link, key=file_name, value=_error.replace("。", "")
                        )
                    else:
                        raise Exception("不支持或被忽略的类型。")
                except Exception as _:
                    DownloadTask.set_error(link=link, value=_error.replace("。", ""))
                    console.log(
                        f"{_t(KeyWord.DOWNLOAD_TASK)}"
                        f'{_t(KeyWord.CHANNEL)}:"{chat_id}",'  # 频道名。
                        f'{_t(KeyWord.LINK)}:"{link}",'  # 链接。
                        f"{_t(KeyWord.LINK_TYPE)}:{_error}"  # 链接类型。
                    )
            self.queue.put_nowait(_task) if _task else None

    def __check_download_finish(
        self,
        message: pyrogram.types.Message,
        sever_file_size: int,
        temp_file_path: str,
        save_directory: str,
        with_move: bool = True,
    ) -> bool:
        """检测文件是否下完。"""
        temp_ext: str = ".temp"
        local_file_size: int = get_file_size(
            file_path=temp_file_path, temp_ext=temp_ext
        )
        format_local_size: str = MetaData.suitable_units_display(local_file_size)
        format_sever_size: str = MetaData.suitable_units_display(sever_file_size)
        _file_path: str = os.path.join(
            save_directory, split_path(temp_file_path).get("file_name")
        )
        file_path: str = (
            _file_path[: -len(temp_ext)]
            if _file_path.endswith(temp_ext)
            else _file_path
        )
        if compare_file_size(a_size=local_file_size, b_size=sever_file_size):
            if with_move:
                result: str = move_to_save_directory(
                    temp_file_path=temp_file_path, save_directory=save_directory
                ).get("e_code")
                log.warning(result) if result is not None else None
            console.log(
                f"{_t(KeyWord.DOWNLOAD_TASK)}"
                f'{_t(KeyWord.FILE)}:"{file_path}",'
                f"{_t(KeyWord.SIZE)}:{format_local_size},"
                f"{_t(KeyWord.TYPE)}:{_t(self.app.get_file_type(message, temp_file_path, DownloadStatus.SUCCESS))},"
                f"{_t(KeyWord.STATUS)}:{_t(DownloadStatus.SUCCESS)}。",
            )
            return True
        console.log(
            f"{_t(KeyWord.DOWNLOAD_TASK)}"
            f'{_t(KeyWord.FILE)}:"{file_path}",'
            f"{_t(KeyWord.ERROR_SIZE)}:{format_local_size},"
            f"{_t(KeyWord.ACTUAL_SIZE)}:{format_sever_size},"
            f"{_t(KeyWord.TYPE)}:{_t(self.app.get_file_type(message, temp_file_path, DownloadStatus.FAILURE))},"
            f"{_t(KeyWord.STATUS)}:{_t(DownloadStatus.FAILURE)}。"
        )
        return False

    @DownloadTask.on_complete
    def download_complete_callback(
        self,
        sever_file_size,
        temp_file_path,
        link,
        message,
        file_name,
        retry_count,
        file_id,
        format_file_size,
        task_id,
        with_upload,
        diy_download_type,
        _future,
        telegram_task_id=None,  # Telegram 进度任务 ID
        telegram_chat_id=None,  # Telegram 聊天 ID
    ):
        if task_id is None:
            if retry_count == 0:
                console.log(
                    f"{_t(KeyWord.DOWNLOAD_TASK)}"
                    f'{_t(KeyWord.ALREADY_EXIST)}:"{_future}"'
                )
                console.log(
                    f"{_t(KeyWord.DOWNLOAD_TASK)}"
                    f'{_t(KeyWord.FILE)}:"{file_name}",'
                    f"{_t(KeyWord.SIZE)}:{format_file_size},"
                    f"{_t(KeyWord.TYPE)}:{_t(self.app.get_file_type(message, file_name, DownloadStatus.SKIP))},"
                    f"{_t(KeyWord.STATUS)}:{_t(DownloadStatus.SKIP)}。",
                    style="#e6db74",
                )
                if self.uploader:
                    self.uploader.download_upload(
                        with_upload=with_upload,
                        file_path=os.path.join(
                            self.env_save_directory(message), file_name
                        ),
                    )
        else:
            self.app.current_task_num -= 1
            self.event.set()  # v1.3.4 修复重试下载被阻塞的问题。
            self.queue.task_done()
            if self.__check_download_finish(
                message=message,
                sever_file_size=sever_file_size,
                temp_file_path=temp_file_path,
                save_directory=self.env_save_directory(message),
                with_move=True,
            ):
                # 更新 Telegram 进度为完成
                if telegram_task_id and telegram_chat_id:
                    tracker = self._get_progress_tracker(telegram_chat_id)
                    if tracker:
                        asyncio.create_task(
                            tracker.complete_progress(telegram_task_id, file_name, success=True)
                        )
                MetaData.print_current_task_num(
                    prompt=_t(KeyWord.CURRENT_DOWNLOAD_TASK),
                    num=self.app.current_task_num,
                )
                if self.uploader:
                    self.uploader.download_upload(
                        with_upload=with_upload,
                        file_path=os.path.join(
                            self.env_save_directory(message), file_name
                        ),
                    )
            else:
                if retry_count < self.app.max_download_retries:
                    retry_count += 1
                    task = self.loop.create_task(
                        self.create_download_task(
                            message_ids=link if isinstance(link, str) else message,
                            retry={"id": file_id, "count": retry_count},
                            with_upload=with_upload,
                            diy_download_type=diy_download_type,
                        )
                    )
                    task.add_done_callback(
                        partial(
                            self.__retry_call,
                            f'{_t(KeyWord.RE_DOWNLOAD)}:"{file_name}",'
                            f"{_t(KeyWord.RETRY_TIMES)}:{retry_count}/{self.app.max_download_retries}。",
                        )
                    )
                else:
                    # 更新 Telegram 进度为失败
                    if telegram_task_id and telegram_chat_id:
                        tracker = self._get_progress_tracker(telegram_chat_id)
                        if tracker:
                            asyncio.create_task(
                                tracker.complete_progress(telegram_task_id, file_name, success=False)
                            )
                    _error = f"(达到最大重试次数:{self.app.max_download_retries}次)。"
                    console.log(
                        f"{_t(KeyWord.DOWNLOAD_TASK)}"
                        f'{_t(KeyWord.FILE)}:"{file_name}",'
                        f"{_t(KeyWord.SIZE)}:{format_file_size},"
                        f"{_t(KeyWord.TYPE)}:{_t(self.app.get_file_type(message, file_name, DownloadStatus.FAILURE))},"
                        f"{_t(KeyWord.STATUS)}:{_t(DownloadStatus.FAILURE)}"
                        f"{_error}"
                    )
                    DownloadTask.set_error(
                        link=link, key=file_name, value=_error.replace("。", "")
                    )
                    self.bot_task_link.discard(link)
                link, file_name = None, None
            self.pb.progress.remove_task(task_id=task_id)
        return link, file_name

    async def download_chat(self, chat_id: str):
        _filter = Filter()
        download_chat_filter: Union[dict, None] = None
        for i in self.download_chat_filter:
            if chat_id == i:
                download_chat_filter = self.download_chat_filter.get(chat_id)
        if not download_chat_filter:
            return None
        if not isinstance(download_chat_filter, dict):
            return None
        chat_id: Union[str, int] = int(chat_id) if chat_id.startswith("-") else chat_id
        date_filter = download_chat_filter.get("date_range")
        start_date = date_filter.get("start_date")
        end_date = date_filter.get("end_date")
        download_type: dict = download_chat_filter.get("download_type")
        links: list = []
        async for message in self.app.client.get_chat_history(
            chat_id=chat_id, reverse=True
        ):
            if _filter.date_range(message, start_date, end_date) and _filter.dtype(
                message, download_type
            ):
                links.append(message.link if message.link else message)
        for link in links:
            await self.create_download_task(
                message_ids=link,
                single_link=True,
                diy_download_type=[_ for _ in DownloadType()],
            )

    @DownloadTask.on_create_task
    async def create_download_task(
        self,
        message_ids: Union[pyrogram.types.Message, str],
        retry: Union[dict, None] = None,
        single_link: bool = False,
        with_upload: Union[dict, None] = None,
        diy_download_type: Optional[list] = None,
    ) -> dict:
        retry = retry if retry else {"id": -1, "count": 0}
        diy_download_type = (
            [_ for _ in DownloadType()] if with_upload else diy_download_type
        )
        try:
            if isinstance(message_ids, pyrogram.types.Message):
                chat_id = message_ids.chat.id
                meta: dict = {
                    "link_type": LinkType.SINGLE,
                    "chat_id": chat_id,
                    "message": message_ids,
                    "member_num": 1,
                }
                link = canonical_link_message(message_ids)
            else:
                meta: dict = await get_message_by_link(
                    client=self.app.client, link=message_ids, single_link=single_link
                )
                link = canonical_link_str(message_ids)

            link_type, chat_id, message, member_num = meta.values()
            DownloadTask.set(link, "link_type", link_type)
            DownloadTask.set(link, "member_num", member_num)
            await self.__add_task(
                chat_id, link_type, link, message, retry, with_upload, diy_download_type
            )
            return {
                "chat_id": chat_id,
                "member_num": member_num,
                "link_type": link_type,
                "status": DownloadStatus.DOWNLOADING,
                "e_code": None,
            }
        except UnicodeEncodeError as e:
            return {
                "chat_id": None,
                "member_num": 0,
                "link_type": None,
                "status": DownloadStatus.FAILURE,
                "e_code": {
                    "all_member": str(e),
                    "error_msg": "频道标题存在特殊字符,请移步终端下载",
                },
            }
        except MsgIdInvalid as e:
            return {
                "chat_id": None,
                "member_num": 0,
                "link_type": None,
                "status": DownloadStatus.FAILURE,
                "e_code": {"all_member": str(e), "error_msg": "消息不存在,可能已删除"},
            }
        except UsernameInvalid as e:
            return {
                "chat_id": None,
                "member_num": 0,
                "link_type": None,
                "status": DownloadStatus.FAILURE,
                "e_code": {
                    "all_member": str(e),
                    "error_msg": "频道用户名无效,该链接的频道用户名可能已更改或频道已解散",
                },
            }
        except ChannelInvalid as e:
            return {
                "chat_id": None,
                "member_num": 0,
                "link_type": None,
                "status": DownloadStatus.FAILURE,
                "e_code": {
                    "all_member": str(e),
                    "error_msg": "频道可能为私密频道或话题频道,请让当前账号加入该频道后再重试",
                },
            }
        except ChannelPrivate_400 as e:
            return {
                "chat_id": None,
                "member_num": 0,
                "link_type": None,
                "status": DownloadStatus.FAILURE,
                "e_code": {
                    "all_member": str(e),
                    "error_msg": "频道可能为私密频道或话题频道,当前账号可能已不在该频道,请让当前账号加入该频道后再重试",
                },
            }
        except ChannelPrivate_406 as e:
            return {
                "chat_id": None,
                "member_num": 0,
                "link_type": None,
                "status": DownloadStatus.FAILURE,
                "e_code": {
                    "all_member": str(e),
                    "error_msg": "频道为私密频道,无法访问",
                },
            }
        except BotMethodInvalid as e:
            res: bool = safe_delete(
                file_p_d=os.path.join(self.app.DIRECTORY_NAME, "sessions")
            )
            error_msg: str = (
                "已删除旧会话文件" if res else "请手动删除软件目录下的sessions文件夹"
            )
            return {
                "chat_id": None,
                "member_num": 0,
                "link_type": None,
                "status": DownloadStatus.FAILURE,
                "e_code": {
                    "all_member": str(e),
                    "error_msg": "检测到使用了「bot_token」方式登录了主账号的行为,"
                    f"{error_msg},重启软件以「手机号码」方式重新登录",
                },
            }
        except ValueError as e:
            return {
                "chat_id": None,
                "member_num": 0,
                "link_type": None,
                "status": DownloadStatus.FAILURE,
                "e_code": {"all_member": str(e), "error_msg": "没有找到有效链接"},
            }
        except UsernameNotOccupied as e:
            return {
                "chat_id": None,
                "member_num": 0,
                "link_type": None,
                "status": DownloadStatus.FAILURE,
                "e_code": {"all_member": str(e), "error_msg": "频道不存在"},
            }
        except Exception as e:
            log.exception(e)
            return {
                "chat_id": None,
                "member_num": 0,
                "link_type": None,
                "status": DownloadStatus.FAILURE,
                "e_code": {"all_member": str(e), "error_msg": "未收录到的错误"},
            }

    def __process_links(self, link: Union[str, list]) -> Union[set, None]:
        """将链接(文本格式或链接)处理成集合。"""
        start_content: str = "https://t.me/"
        links: set = set()
        if isinstance(link, str):
            if link.endswith(".txt") and os.path.isfile(link):
                with open(file=link, mode="r", encoding="UTF-8") as _:
                    _links: list = [content.strip() for content in _.readlines()]
                for i in _links:
                    if i.startswith(start_content):
                        links.add(i)
                        self.bot_task_link.add(i)
                        try:
                            self.bot_task_link_canon.add(canonical_link_str(i))
                        except Exception:
                            pass
                    elif i == "" or i.startswith("#"):
                        # 空行或以#开头的注释行
                        continue
                    else:
                        log.warning(
                            f'"{i}"是一个非法链接,{_t(KeyWord.STATUS)}:{_t(DownloadStatus.SKIP)}。'
                        )
            elif link.startswith(start_content):
                links.add(link)
        elif isinstance(link, list):
            for i in link:
                _link: Union[set, None] = self.__process_links(link=i)
                if _link is not None:
                    links.update(_link)
        if links:
            return links
        elif not self.app.bot_token:
            console.log("没有找到有效链接,程序已退出。", style="#FF4689")
            sys.exit(0)
        else:
            console.log("没有找到有效链接。", style="#FF4689")
            return None

    @staticmethod
    def __retry_call(notice, _future):
        console.log(notice, style="#FF4689")

    async def __download_media_from_links(self) -> None:
        await self.app.client.start(use_qr=False)
        self.pb.progress.start()  # v1.1.8修复登录输入手机号不显示文本问题。
        if self.app.bot_token is not None:
            result = await self.start_bot(
                self.app.client,
                pyrogram.Client(
                    name=self.BOT_NAME,
                    api_hash=self.app.api_hash,
                    api_id=self.app.api_id,
                    bot_token=self.app.bot_token,
                    workdir=self.app.work_directory,
                    proxy=self.app.proxy if self.app.enable_proxy else None,
                    sleep_threshold=SLEEP_THRESHOLD,
                ),
            )
            console.log(result, style="#B1DB74" if self.is_bot_running else "#FF4689")
            if self.is_bot_running:
                self.uploader = TelegramUploader(
                    client=self.app.client,
                    loop=self.loop,
                    is_premium=self.app.client.me.is_premium,
                    progress=self.pb,
                    max_upload_task=self.app.max_upload_task,
                    max_retry_count=self.app.max_upload_retries,
                    notify=self.done_notice,
                )
                self.cd = CallbackData()
                if self.gc.upload_delete:
                    console.log(
                        f"在使用监听转发(/listen_forward)时:\n"
                        f'当检测到"受限转发"时,自动采用"下载后上传"的方式,并在完成后删除本地文件。\n'
                        f"如需关闭,前往机器人[帮助页面]->[设置]->[上传设置]进行修改。\n",
                        style="#FF4689",
                    )
        self.is_running = True
        self.running_log.add(self.is_running)
        links: Union[set, None] = self.__process_links(link=self.app.links)
        if links:
            # 使用规范化键与历史完成集比较，避免不同参数形式导致的漏判
            pending_links = [
                link
                for link in links
                if canonical_link_str(link) not in DownloadTask.COMPLETE_LINK
            ]
            [
                await self.loop.create_task(
                    self.create_download_task(message_ids=link, retry=None)
                )
                for link in pending_links
            ]
        # 处理队列中的任务与机器人事件。
        while not self.queue.empty() or self.is_bot_running:
            result = await self.queue.get()
            try:
                await result
            except PermissionError as e:
                log.error(
                    "临时文件无法移动至下载路径:\n"
                    "1.可能存在使用网络路径、挂载硬盘行为(本软件不支持);\n"
                    "2.可能存在多开软件时,同时操作同一文件或目录导致冲突;\n"
                    "3.由于软件设计缺陷,没有考虑到不同频道文件名相同的情况(若调整将会导致部分用户更新后重复下载已有文件),当保存路径下文件过多时,可能恰巧存在相同文件名的文件,导致相同文件名无法正常移动,故请定期整理归档下载链接与保存路径下的文件。"
                    f'{_t(KeyWord.REASON)}:"{e}"'
                )
        # 等待所有任务完成。
        await self.queue.join()
        await self.app.client.stop() if self.app.client.is_connected else None

    def run(self) -> None:
        record_error: bool = False
        try:
            MetaData.print_meta()
            self.app.print_config_table(
                links=self.app.links,
                download_type=self.app.download_type,
                proxy=self.app.proxy,
            )
            self.loop.run_until_complete(self.__download_media_from_links())
        except KeyError as e:
            record_error: bool = True
            if str(e) == "0":
                log.error(
                    "「网络」或「代理问题」,在确保当前网络连接正常情况下检查:\n「VPN」是否可用,「软件代理」是否配置正确。"
                )
                console.print(Issues.PROXY_NOT_CONFIGURED)
                raise SystemExit(0)
            log.exception(f'运行出错,{_t(KeyWord.REASON)}:"{e}"')
        except pyrogram.errors.BadMsgNotification as e:
            record_error: bool = True
            if str(e) in (
                str(pyrogram.errors.BadMsgNotification(16)),
                str(pyrogram.errors.BadMsgNotification(17)),
            ):
                console.print(Issues.SYSTEM_TIME_NOT_SYNCHRONIZED)
                raise SystemExit(0)
            log.exception(f'运行出错,{_t(KeyWord.REASON)}:"{e}"')
        except (SessionRevoked, AuthKeyUnregistered, SessionExpired, Unauthorized) as e:
            log.error(f'登录时遇到错误,{_t(KeyWord.REASON)}:"{e}"')
            res: bool = safe_delete(
                file_p_d=os.path.join(self.app.DIRECTORY_NAME, "sessions")
            )
            record_error: bool = True
            if res:
                log.warning("账号已失效,已删除旧会话文件,请重启软件。")
            else:
                log.error("账号已失效,请手动删除软件目录下的sessions文件夹后重启软件。")
        except (ConnectionError, TimeoutError) as e:
            record_error: bool = True
            if not self.app.enable_proxy:
                log.error(f'网络连接失败,请尝试配置代理,{_t(KeyWord.REASON)}:"{e}"')
                console.print(Issues.PROXY_NOT_CONFIGURED)
            else:
                log.error(f'网络连接失败,请检查VPN是否可用,{_t(KeyWord.REASON)}:"{e}"')
        except AttributeError as e:
            record_error: bool = True
            log.error(f'登录超时,请重新打开软件尝试登录,{_t(KeyWord.REASON)}:"{e}"')
        except KeyboardInterrupt:
            console.log("用户手动终止下载任务。")
        except OperationalError as e:
            record_error: bool = True
            log.error(
                f'检测到多开软件时,由于在上一个实例中「下载完成」后窗口没有被关闭的行为,请在关闭后重试,{_t(KeyWord.REASON)}:"{e}"'
            )
        except Exception as e:
            record_error: bool = True
            log.exception(msg=f'运行出错,{_t(KeyWord.REASON)}:"{e}"')
        finally:
            self.is_running = False
            self.pb.progress.stop()
            if not record_error:
                self.app.print_link_table(
                    link_info=DownloadTask.LINK_INFO,
                    export=self.gc.get_config("export_table").get("link"),
                )
                self.app.print_count_table(
                    export=self.gc.get_config("export_table").get("count")
                )
                MetaData.pay()
                (
                    self.app.process_shutdown(60)
                    if len(self.running_log) == 2
                    else None
                )  # v1.2.8如果并未打开客户端执行任何下载,则不执行关机。
            self.app.ctrl_c()
