# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0]

### Added

- `diagnose`, `snapshot` and `version` commands.
- Read-only collector for pods, warning events, deployments, nodes and failing container logs.
- Detectors: crash loop, OOMKilled, image pull, container config error, unschedulable pods,
  probe failures, stalled rollouts and node problems, with rollout/cause correlation.
- Optional root cause analysis through Anthropic, OpenAI-compatible or Ollama providers,
  configured with environment variables.
- Secret redaction for prompts and reports.
- Markdown and JSON reports, `--fail-on` exit codes.
- Reproducible kind demo with six failure scenarios.
