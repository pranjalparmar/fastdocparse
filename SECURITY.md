# Security Policy

## Supported versions

Only the latest release on [PyPI](https://pypi.org/project/fastdocparse/) is supported. There's no long-term-support branch; upgrade to get a fix.

## Reporting a vulnerability

Please use GitHub's [private vulnerability reporting](https://github.com/pranjalparmar/fastdocparse/security/advisories/new) instead of opening a public issue. That starts a private conversation with the maintainer so a fix can land before the details are public.

You should get a response within a few days. This is a solo-maintained open-source project without a dedicated security team, so please be patient.

## Known risk areas

Two things are already true by design, documented here explicitly rather than left as a surprise:

- **`FASTDOCPARSE_PLUGINS` executes arbitrary Python at CLI startup.** Setting this environment variable imports and runs whatever module(s) it names, with no sandboxing, the same trust model as `PYTHONSTARTUP` or `DJANGO_SETTINGS_MODULE`. This is fine for pointing it at your own plugin on your own machine. Never let this variable be set from an untrusted source, e.g. a request parameter in a hosted service built on top of this CLI. See the docstring on `_load_plugins()` in `src/fastdocparse/cli.py`.
- **The ReDoS guard on user-supplied regex patterns doesn't always run.** `grounding.py`'s `_regex_matches_with_timeout` relies on `SIGALRM`, which doesn't exist on Windows, so a pathological `pattern` in a schema (yours or an LLM-generated one) has no timeout protection there, and it's also bypassed when extraction runs off the main thread, even on platforms that do have `SIGALRM`, since `signal`-based timeouts only work on the main thread. If you call this library from a background thread (e.g. a web server handling requests concurrently), or you're on Windows, treat this guard as absent and validate patterns yourself before accepting schemas from an untrusted source. This is a known, accepted gap (see issue [#28](https://github.com/pranjalparmar/fastdocparse/issues/28)), not a secret one.

If you find something beyond these two, please report it privately rather than assuming it's already known.
