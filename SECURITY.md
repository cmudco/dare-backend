# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | ✅ |
| < 0.1 | ❌ |

Security fixes are backported to the most recent minor release on a best-effort basis.

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues, discussions, or pull requests.**

Instead, use [GitHub's private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability) on this repository. If private reporting is not enabled, contact the repository maintainers privately through the organization profile before disclosing publicly.

### What to include

To help us triage quickly, please include:

- A clear description of the issue and its impact.
- Steps to reproduce, ideally with a minimal proof of concept.
- Affected version(s) and deployment configuration.
- Any suggested mitigation, if known.
- Your name and how you'd like to be credited (or whether you'd prefer to remain anonymous).

### What to expect

| Stage | Target time |
|---|---|
| Acknowledge receipt | 3 business days |
| Initial triage and severity assessment | 7 business days |
| Fix development and testing | varies by severity |
| Coordinated disclosure | typically 30–90 days from report |

We will keep you informed throughout the process and credit you in the release notes unless you ask us not to.

## Scope

In scope:

- The DARE backend Django application
- Default deployment configurations published in this repository
- First-party Python dependencies pinned in `requirements/`

Out of scope:

- Vulnerabilities in third-party LLM providers (report directly to them)
- Vulnerabilities in self-hosted dependencies (Postgres, Redis, Weaviate, Ollama) outside our deployment configuration
- Issues in fork or modified deployments — please report to the operator of the service you found them in
- Social engineering, physical attacks, denial of service via volume

## Safe harbor

We will not pursue or support legal action against researchers who:

- Make a good-faith effort to comply with this policy.
- Avoid privacy violations, destruction of data, and interruption or degradation of services.
- Do not exploit a discovered vulnerability beyond the minimum necessary to demonstrate it.
- Give us reasonable time to remediate before public disclosure.

## Hardening recommendations

For operators deploying DARE, please review the deployment guide ([INSTALL.md](INSTALL.md)) and configuration reference ([docs/configuration.md](docs/configuration.md)). At minimum:

- Rotate `DJANGO_SECRET_KEY` and `DARE_INTERNAL_KEY` from their example values before any non-local deployment.
- Set `DJANGO_DEBUG=False` in production.
- Restrict `ALLOWED_HOSTS` to the hostnames you serve.
- Terminate TLS at a reverse proxy (Nginx, ALB) — do not expose Uvicorn directly.
- Use strong, unique passwords for the database and Redis. Enable Redis AUTH (`REDIS_PASSWORD`).
- Keep LLM provider keys in `.env` (never commit them) and rotate periodically.
- Apply OS and dependency updates regularly.

Thank you for helping keep DARE and its users safe.
