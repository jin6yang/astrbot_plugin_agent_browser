# AstrBot Obscura Agent Browser

AstrBot plugin that uses the local [Obscura](https://github.com/h4ckf0r0day/obscura) binary as a lightweight browser search backend.

## Features

- `/搜索 <问题或URL>` and `/search <query-or-url>` force a browser task.
- `搜索 <问题或URL>` and `search <query-or-url>` prefixes force the same flow without slash commands.
- Registers `obscura_web_search` and `obscura_open_url` as AstrBot LLM Tools so capable models can decide whether to search or open a shared URL during normal chat.
- Uses Obscura CLI on demand; no persistent browser server is started.
- Defaults to DuckDuckGo HTML search and supports a configurable search URL template.
- Extracts page text, OpenGraph metadata, headings, navigation labels, image URLs, alt text, captions, and basic CSS color/font tokens.
- Can optionally call a configured vision provider to describe image URLs.
- Blocks non-HTTP URLs, localhost, and private/reserved IPs by default.

## Obscura Binary

Set `obscura_path` in the plugin config when Obscura is not on PATH. If left empty, the plugin tries:

1. `obscura/obscura.exe` under this plugin directory.
2. `obscura/obscura` under this plugin directory.
3. `obscura` or `obscura.exe` from system PATH.

## Commands

```text
/搜索 AstrBot 插件开发
/search AstrBot plugin development
搜索 AstrBot 插件开发
search AstrBot plugin development
/搜索 https://example.com 总结这个网页
```

## Trigger Strategy

The default mode is hybrid:

- `enable_force_commands=true`: `/搜索` and `/search` force a browser task.
- `enable_force_prefixes=true`: configured prefixes such as `搜索` and `search` force a browser task.
- `enable_llm_tool=true`: the model can call `obscura_web_search` or `obscura_open_url` on its own if it supports tool calling.
- `force_trigger_mode=main_bot`: forced tasks inject evidence into the main bot request, so persona, context, and other plugins still shape the final reply.

Forced tasks parse URLs first. If the message contains `http://` or `https://`, Obscura opens those URLs; otherwise it performs a web search. In ordinary chat, the plugin does not intercept URLs by itself. The main model decides whether to call `obscura_open_url`.

Set `enable_force_prefixes=false` when you want to keep the prefix list but temporarily stop prefix-based triggering.

Use `force_trigger_mode=direct_reply` only when you want the plugin to summarize and reply by itself. In that mode, `summary_provider_id` controls the summarizer model.

`auto_search_policy=always` is available for testing or narrow deployments, but it runs a browser task on every normal message and is usually wasteful.

## Prompt Customization

Forced browser tasks use two different prompts:

- `forced_evidence_prompt_template`: used by `main_bot` mode to wrap forced-task evidence for the main bot.
- `summary_prompt_template` or `summary_prompt_file`: used only by `direct_reply` mode.

`summary_prompt_source=file` reads `summary_prompt_file`. `summary_prompt_source=config` reads `summary_prompt_template`. The plugin uses the selected source directly and does not fallback to another source. Templates must contain `{query}` and `{evidence}`; they can also use `{summary_focus}`.

## Media and Design Evidence

`enable_media_extraction` controls whether media evidence is collected. `media_extract_mode` controls how much media evidence is added:

- `metadata_only`: image URLs, alt/title text, captions, OpenGraph metadata, headings, nav labels, links, and simple CSS tokens.
- `images`: same as `metadata_only`, plus optional image captions when `image_caption_provider_id` points to a vision-capable provider.

Obscura does not provide full screenshot-level visual understanding here. Without `visual_caption`, the model should treat media evidence as metadata only.

## Tests

The unit tests use only the Python standard library:

```powershell
python -m unittest discover -s tests
```
