### Added
- **`gtd-mail attachments --save/--telegram` screens downloads with sksecurity.**
  Malicious files go to `quarantine/` (0600) with a ledger line and are not sent
  to Telegram; suspicious files forward with a warning prefix. A missing screener
  never blocks mail. Needs `sksecurity screen-file` (degrades gracefully without it).

### Fixed
- Attachment filenames are reduced to their basename so a `../` name cannot
  escape the save directory.
