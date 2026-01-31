# Draft: 关键词过滤功能

## 用户需求

实现关键词过滤功能，用于Telegram频道消息下载：

1. **支持标签关键词**：如#萝莉、#女主K、#巨乳
2. **支持文本关键词**：如会员专属福利
3. **支持多个关键词**：OR逻辑，包含任意一个即下载
4. **应用场景**：
   - `/download_chat` 命令：批量下载时过滤
   - `/listen_download` 命令：订阅时过滤

## 代码库上下文（已收集）

### 现有Filter类（module/filter.py）
- `date_range()` 方法：检查消息日期是否在范围内
- `dtype()` 方法：检查消息文件类型是否匹配
- 返回值逻辑：True=通过，False=过滤掉

### Bot UI模式
- 使用 InlineKeyboardMarkup 创建内联键盘
- download_chat 相关方法：
  - `download_chat_filter_button()` - 主过滤设置界面
  - `toggle_download_chat_type_filter_button()` - 文件类型设置
  - `filter_date_range_button()` - 日期范围设置

### 配置结构
```python
self.download_chat_filter[chat_id] = {
    "date_range": {"start_date": None, "end_date": None, "adjust_step": 1},
    "download_type": {"video": True, "photo": True, ...}
}
```

### download_chat 消息处理（downloader.py:2662）
- 遍历频道消息
- 应用多个过滤器（AND逻辑）
- 获取消息文本：`message.text` 和 `message.caption`

### listen_download 配置
```python
self.listen_download_chat: dict = {}
self.listen_download_tag_by_chatid: Dict[Union[int, str], str] = {}
```

### 测试基础设施
- **无测试文件**：项目目前没有测试套件
- 需要手动验证

## 研究发现

### 1. 文本匹配性能对比
```
场景                    推荐方法                特点
---------------------------------------------------------------
<20个关键词            简单字符串包含           最快，实现简单
20-100个关键词         预编译正则表达式        平衡性能与灵活性
>100个关键词           Aho-Corasick算法       需要额外依赖，性能最佳
```

### 2. Telegram消息文本处理
- `message.text`：纯文本消息（只有文字的消息）
- `message.caption`：媒体消息描述（图片/视频的附带文字）
- **两者都可能为None**，需要优雅处理：`(message.text or message.caption or "")`

### 3. 标签关键词匹配模式
- 标签以 `#` 开头
- 支持中文：`\u4e00-\u9fff`
- 可能包含下划线、数字、字母：`[\w\u4e00-\u9fff]+`

### 4. 现有项目模式
- 统一使用 `UTF-8` 编码
- 优先使用类型提示
- Filter类使用静态方法模式
- 配置使用字典结构，支持嵌套

### 5. 测试基础设施
- **无测试文件**：项目目前没有测试套件
- 需要手动验证

## 待确认问题
1. 关键词如何设置？UI交互还是命令行？
2. listen_download 的关键词配置方式？
3. 关键词大小写敏感性问题？
4. 是否需要同时检查 text 和 caption？
5. 关键词数量限制？
