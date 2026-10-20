# Security policy

pacod-ap holds people's accounts (through pacod-id), their conversations, every agent's knowledge
base and the keys that reach the LLM gateway. Reports of weaknesses are welcome and are handled
privately until a fix is out.

## Reporting a vulnerability

Do not open a public issue. Use GitHub's private vulnerability reporting: the repository's
**Security** tab, then **Report a vulnerability**.

Please include:

- the version or commit, and which part is affected: the chat API, the admin API, the MCP
  server, ingestion, the workers (improvement loop, newsletter), the SvelteKit frontend, or the
  deployment files (compose, Caddy, monitoring);
- what an attacker needs (a signed-in account, an admin role, network access to `pacod-net`,
  nothing);
- the steps to reproduce, and what happens compared with what should happen;
- whether you have told anyone else.

Never send real keys, tokens, passwords or other people's conversations. Placeholders are enough.

## What happens next

The maintainers confirm that the report arrived, agree on a disclosure date with you, and tell you
when the fix is released. You are credited in the release notes unless you prefer not to be.

There are no fixed response times; the maintainers answer as soon as they can.

## Supported versions

Until version 1.0, only the latest release receives security fixes.

## Scope

In scope: the code in this repository and the deployment files it ships (`pacod-api/`,
`pacod-ui/`, `monitoring/`, `deploy/`, `scripts/`), with their defaults.

Out of scope: pacod-gw and pacod-id themselves (report those to their own projects), the LLM
providers, and settings an operator chose against the documentation — for example dev tokens
(`AUTH_ALLOW_DEV_TOKENS=true`) enabled on a deployed server. Defaults that make such a mistake easy
are in scope.
