# coding=UTF-8
# Author:Gentlesprite
# Software:PyCharm
# Time:2025/9/25 1:22
# File:filter.py
import datetime
from typing import Optional

import pyrogram


class Filter:
    @staticmethod
    def date_range(
            message: pyrogram.types.Message,
            start_date: Optional[float],
            end_date: Optional[float]
    ) -> bool:
        if start_date and end_date:
            return start_date <= datetime.datetime.timestamp(message.date) <= end_date
        elif start_date:
            return start_date <= datetime.datetime.timestamp(message.date)
        elif end_date:
            return datetime.datetime.timestamp(message.date) <= end_date
        return True

    @staticmethod
    def dtype(
            message: pyrogram.types.Message,
            download_type: dict
    ) -> bool:
        table: list = []
        for dtype, status in download_type.items():
            if getattr(message, dtype) and status:
                table.append(True)
            table.append(False)
        if True in table:
            return True
        return False
    
    @staticmethod
    def keywords(
            message: pyrogram.types.Message,
            keywords: list
    ) -> bool:
        """关键词过滤：检查消息文本是否包含任意一个关键词（OR逻辑）"""
        if not keywords or len(keywords) == 0:
            # 关键词列表为空，不过滤
            return True
        
        # 获取消息的文本（text + caption）
        # message.text: 纯文本消息
        # message.caption: 媒体文件的说明文字
        message_text = message.text or ""
        message_caption = message.caption or ""
        full_text = f"{message_text} {message_caption}"
        
        # 不区分大小写匹配
        full_text_lower = full_text.lower()
        
        # 检查是否包含任意一个关键词
        for keyword in keywords:
            if not keyword:
                continue
            keyword_lower = keyword.lower().strip()
            if keyword_lower in full_text_lower:
                return True
        
        # 没有匹配到任何关键词
        return False
