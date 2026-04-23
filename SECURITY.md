# Security Policy

## Reporting a Vulnerability

If you discover a security issue in FlowClone, please **do not** open a public GitHub issue.

Instead, report it privately via one of:

- **GitHub Security Advisories** — [open a private advisory](../../security/advisories/new) on this repository.
- **Email** — `archie.mcdonald2008@gmail.com`

Please include:

- A description of the issue and its impact.
- Steps to reproduce.
- Any relevant version info (OS, Python version, FlowClone commit / release tag).

I'll acknowledge receipt within a few days and work with you on disclosure. If the issue is confirmed, I'll credit you in the release notes unless you'd prefer to remain anonymous.

## Scope

FlowClone is a personal tool that captures microphone audio, hooks global keyboard input, and sends data to the LLM provider you configure (OpenAI or Groq). In scope:

- Arbitrary code execution through malformed config, dictionary, or hotkey input.
- API-key leakage via error messages, logs, or clipboard handling.
- Clipboard or keyboard-hook abuse beyond the documented behaviour.
- Vulnerabilities in the build/packaging pipeline (`pyinstaller` config).

Out of scope:

- Issues in upstream dependencies (report those to their maintainers).
- The user setting a malicious `provider_base_url` via hand-edited `config.json` — this is equivalent to modifying the source, since an attacker with local file access can do anything anyway.
- Denial of service by holding the hotkey indefinitely (just release the key).

## Supported Versions

Only the latest `main` branch is supported. If you're running an older tagged release, please update before filing a report.
