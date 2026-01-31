# Draft: Add %CHAT_USERNAME% Save Directory Placeholder

## User's Goal
Save downloaded files to folders named by channel username instead of numeric chat ID.

## Example
- Link: `https://t.me/mianmia1/1177`
- Current: saves to `1234567890/` (chat.id)
- Expected: saves to `mianmia1/` (chat.username)

## Requirements (from user)

### New Placeholder
- Add `%CHAT_USERNAME%` to `SaveDirectoryPrefix` enum
- Location: `module/enums.py`
- Value: `CHAT_USERNAME: str = "%CHAT_USERNAME%"`

### Modify env_save_directory()
- Add handling for `%CHAT_USERNAME%` placeholder
- Get `message.chat.username`
- Fallback to `chat.id` if username is missing

### Fallback Logic
1. Use `chat.username` if available
2. If username is None or empty string, fallback to `chat.id`
3. Also consider `chat.title` as fallback option (question for user)

### Compatibility
- Keep existing `%CHAT_ID%` and `%MIME_TYPE%` working
- Don't affect existing tag subdirectory logic
- Backward compatible: no behavior change if user doesn't modify config

## Technical Decisions (TBD)
- Fallback priority: username > ??? > id
- How to handle Chinese channel titles
- Should we sanitize username (for file system safety)

## Open Questions
- Fallback order: username → title → id OR username → id?
- Should we sanitize the username for file system safety?
- Private channels handling: use title or id?

## Research In Progress
- SaveDirectoryPrefix enum structure
- env_save_directory() implementation
- Pyrogram Chat object attributes
- Existing validation patterns
