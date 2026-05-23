# AstrBot Obscura Agent Browser

AstrBot plugin that uses the local [Obscura](https://github.com/h4ckf0r0day/obscura) binary as a lightweight browser search backend.

## Features

- `/搜索 <问题>` and `/search <query>` force a browser search and return a source-grounded summary.
- `搜索 <问题>` and `search <query>` prefixes force the same flow without slash commands.
- Registers `obscura_web_search` as an AstrBot LLM Tool so capable models can decide to search during normal chat.
- Uses Obscura CLI on demand; no persistent browser server is started.
- Defaults to DuckDuckGo HTML search and supports a configurable search URL template.
- Blocks non-HTTP URLs, localhost, and private/reserved IPs by default.

## Obscura Binary

Set `obscura_path` in the plugin config when Obscura is not on PATH. If left empty, the plugin tries:

1. `obscura-x86_64-windows/obscura.exe` under this plugin directory.
2. `obscura` or `obscura.exe` from system PATH.

## Commands

```text
/搜索 AstrBot 插件开发
/search AstrBot plugin development
搜索 AstrBot 插件开发
search AstrBot plugin development
```

## Tests

The unit tests use only the Python standard library:

```powershell
python -m unittest discover -s tests
```
