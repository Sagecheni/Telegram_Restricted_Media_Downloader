# AGENTS.md

This file contains guidelines for agentic coding agents working in this repository.

## Project Overview

**Project**: Telegram Restricted Media Downloader (TRMD)
**Language**: Python 3.12+ (Recommended: 3.13.2)
**Main Entry Point**: `python main.py`
**Framework**: Pyrogram (forked as kurigram v2.2.15) for Telegram API

## Build & Development Commands

### Running the Application
```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python main.py

# Build standalone executable (using Nuitka)
python build.py
```

### Running Tests
**Note**: This project does not currently have a test suite. When adding tests, place them in a `tests/` directory.

### Building Executable
```bash
python build.py
```
This uses Nuitka to compile to a standalone binary. The build script automatically installs required dependencies.

## Code Style Guidelines

### File Headers
Every Python file should begin with:
```python
# coding=UTF-8
# Author:Gentlesprite
# Software:PyCharm
# Time:YYYY/M/D H:MM
# File:filename.py
```

### Import Organization
Imports should be grouped in this order:
1. Standard library imports
2. Third-party imports
3. Local module imports (`from module import ...`)

Example:
```python
import os
import asyncio
from typing import Union, Optional

import pyrogram
from rich.console import Console

from module import log, console
from module.enums import DownloadType
```

### Naming Conventions
- **Classes**: `PascalCase` - `Application`, `UserConfig`, `TelegramRestrictedMediaDownloader`
- **Functions/Methods**: `snake_case` - `get_file_type`, `process_shutdown`
- **Constants**: `UPPER_SNAKE_CASE` - `SOFTWARE_FULL_NAME`, `AUTHOR`, `LOG_PATH`
- **Variables**: `snake_case` - `client`, `message`, `download_type`
- **Private methods**: `_underscore_prefix` (single), `__double_underscore` (internal)
- **Type aliases**: `PascalCase` - `Union[str, None]`

### Type Hints
Use type hints consistently for function signatures:
```python
def process_download(self, message: pyrogram.types.Message) -> bool:
    pass

def get_config(self, param: str) -> Union[str, None]:
    pass
```

Common imports from typing:
- `Union`, `Optional`, `List`, `Dict`, `Set`, `Tuple`
- Use `Union[str, None]` or `Optional[str]` interchangeably

### Error Handling
- Use specific exceptions in except blocks when possible
- Always use the global `log` object (from `module`) for logging errors
- Use `log.error()` for errors, `log.warning()` for warnings, `log.info()` for informational messages
- Wrap exceptions that should be caught and handled gracefully

```python
try:
    # operation
except Exception as e:
    log.error(f'Error description,{_t(KeyWord.REASON)}:"{e}"')
```

### Logging
The project uses Python's logging module with Rich for console output:
- Use `log` from `module` (not `logging` directly)
- Use `console` from `module` for rich console output
- File logs: `INFO` level and above
- Console logs: `WARNING` level and above by default (configurable)

### Formatting & Spacing
- Use 4 spaces for indentation (no tabs)
- Blank lines between logical sections
- Two blank lines between top-level definitions
- One blank line between method definitions

### String Formatting
- Use f-strings for simple cases: `f"{value}"`
- Use `.format()` for complex formatting: `'{} - {}.{}'.format(...)`
- Use concatenation when building multi-line strings: `line1 + line2`

### YAML Configuration
- Use `yaml.safe_load()` for reading
- Use `yaml.dump()` for writing
- For None values, use the custom `CustomDumper` which represents None as `~`
- Configuration files are UTF-8 encoded

### Async/Await Patterns
- This is an async application using `asyncio`
- Use `async def` for coroutines
- Use `await` when calling coroutines
- Use `asyncio.gather()` for concurrent operations
- Use `asyncio.create_subprocess_exec()` for running external commands

### File Path Handling
- Use `os.path.join()` for path construction (cross-platform)
- Use `os.makedirs(path, exist_ok=True)` to create directories
- Always use absolute paths where possible
- File encoding: always specify `encoding='UTF-8'` when opening files

### Configuration Management
- User config: `config.yaml` (project root)
- Global config: `.CONFIG.yaml` (in APPDATA path: `%APPDATA%/TRMD` on Windows, `~/.config/TRMD` on Linux)
- Config template: `UserConfig.TEMPLATE` for user config
- Use `UserConfig` and `GlobalConfig` classes from `module.config` for accessing configs

### Working with Pyrogram
- Use `pyrogram.Client` for Telegram API interaction
- Use `pyrogram.handlers.MessageHandler` for message handlers
- Use `pyrogram.filters` for message filtering
- Use `pyrogram.types` for Telegram types
- The project uses a fork called `kurigram` (v2.2.15)

### Project-Specific Patterns

#### Progress Tracking
Use `ProgressBar` from `module.stdio` for progress bars:
```python
from module.stdio import ProgressBar
pb = ProgressBar()
```

#### File Naming
File names for downloads follow this pattern: `{message_id} - {title}.{extension}`

#### Link Parsing
Use utility functions from `module.util`:
- `parse_link()` - Parse Telegram links
- `safe_message()` - Safely format messages
- `format_chat_link()` - Format chat links
- `get_message_by_link()` - Get message by link

#### Path Utilities
Use functions from `module.path_tool`:
- `validate_title()` - Validate/sanitize file names
- `truncate_filename()` - Truncate long filenames
- `get_extension()` - Get file extension
- `move_to_save_directory()` - Move file to save directory
- `safe_delete()` - Safely delete files

### Constants & Configuration Keys
Use constants from `module.enums` for:
- `DownloadType` - VIDEO, PHOTO, DOCUMENT, AUDIO, VOICE, ANIMATION
- `DownloadStatus` - SUCCESS, FAILURE, SKIP, DOWNLOADING
- `KeyWord` - REASON, etc.
- `BotCommandText`, `BotMessage`, `BotCallbackText`, `BotButton` - Bot-related strings

### Testing Considerations
- Currently no tests exist in the project
- When adding tests, place them in `tests/` directory
- Use pytest framework if tests are added
- Mock external dependencies like Pyrogram client

### External Dependencies
Key third-party libraries:
- **Pyrogram (kurigram)**: Telegram client library
- **yt-dlp**: Video downloading (for external links like Twitter)
- **gallery-dl**: Image/video downloading for external sites
- **aiohttp**: Async HTTP client
- **Rich**: Terminal formatting and progress bars
- **PyYAML**: Configuration file handling
- **tgcrypto**: Telegram encryption

### Chinese Comments & Messages
This project has Chinese comments and UI messages. When modifying code:
- Preserve existing Chinese comments unless they need translation
- Keep log messages and user-facing text in Chinese
- Use proper encoding: `# coding=UTF-8` at file start

### Important Notes
- The project supports Windows and Linux
- Configuration paths differ between platforms
- Uses Nuitka for building standalone executables
- Has a bot feature that can be optionally enabled with `bot_token`
- Supports downloading from Telegram, Twitter/X, Instagram, and Iwara
- Has listen mode for real-time downloading
- Supports upload functionality to Telegram channels
