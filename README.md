# AstrBot Obscura Agent Browser

AstrBot plugin that uses the local [Obscura](https://github.com/h4ckf0r0day/obscura) binary as a lightweight browser search backend.

## Features

- `/搜索 <问题>` and `/search <query>` force a browser search and return a source-grounded summary.
- `搜索 <问题>` and `search <query>` prefixes force the same flow without slash commands.
- Registers `obscura_web_search` as an AstrBot LLM Tool so capable models can decide to search during normal chat.
- Uses Obscura CLI on demand; no persistent browser server is started.
- Defaults to DuckDuckGo HTML search and supports a configurable search URL template.
- Extracts page text, OpenGraph metadata, headings, navigation labels, image URLs, alt text, captions, and basic CSS color/font tokens.
- Can optionally call a configured vision provider to describe image URLs.
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

## Trigger Strategy

The default mode is hybrid:

- `enable_force_commands=true`: `/搜索` and `/search` force browser search.
- `enable_force_prefixes=true`: configured prefixes such as `搜索` and `search` force browser search.
- `enable_llm_tool=true`: the model can call `obscura_web_search` on its own if it supports tool calling.

Set `enable_force_prefixes=false` when you want to keep the prefix list but temporarily stop prefix-based triggering.

`auto_search_policy=always` is available for testing or narrow deployments, but it searches on every normal message and is usually wasteful.

## Prompt Customization

The summary prompt is loaded in this order:

1. `summary_prompt_file`, defaulting to `prompts/summary.md`.
2. `summary_prompt_template` from the WebUI config.
3. The built-in default prompt.

The template must contain `{query}` and `{evidence}`. It can also use `{summary_focus}`.

## Media and Design Evidence

`media_extract_mode` controls extra page evidence:

- `off`: only text excerpts.
- `metadata_only`: image URLs, alt/title text, captions, OpenGraph metadata, headings, nav labels, links, and simple CSS tokens.
- `images`: same as `metadata_only`, plus optional image captions when `enable_image_caption=true` and `image_caption_provider_id` points to a vision-capable provider.

Obscura does not provide full screenshot-level visual understanding here. Without `visual_caption`, the model should treat media evidence as metadata only.

## Tests

The unit tests use only the Python standard library:

```powershell
python -m unittest discover -s tests
```
