# Security policy

Use this to report a vulnerability in the DARE backend and to harden your own deployment.

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |
| < 0.1 | No |

Security fixes are backported to the most recent minor release on a best-effort basis.

## Reporting a vulnerability

Do not report security vulnerabilities through public GitHub issues, discussions or pull requests.

Use [GitHub's private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
on this repository. If private reporting is not enabled, email vks@andrew.cmu.edu before
disclosing anything publicly.

### What to include

- A clear description of the issue and its impact.
- Steps to reproduce, ideally with a minimal proof of concept.
- Affected versions and deployment configuration.
- Any suggested mitigation, if known.
- Your name and how you want to be credited, or that you prefer to stay anonymous.

### What to expect

| Stage | Target time |
| --- | --- |
| Acknowledge receipt | 3 business days |
| Initial triage and severity assessment | 7 business days |
| Fix development and testing | Varies by severity |
| Coordinated disclosure | Typically 30–90 days from report |

We keep you informed throughout and credit you in the release notes unless you ask us not to.

## Scope

In scope:

- The DARE backend Django application
- Default deployment configurations published in this repository
- First-party Python dependencies pinned in `requirements/`

Out of scope:

- Vulnerabilities in third-party LLM providers (report them to the provider)
- Vulnerabilities in self-hosted dependencies (Postgres, Redis, Weaviate, Ollama) outside our
  deployment configuration
- Issues in forks or modified deployments (report them to the operator of that service)
- Social engineering, physical attacks, and denial of service by volume

## Safe harbor

We will not pursue or support legal action against researchers who:

- Make a good-faith effort to comply with this policy.
- Avoid privacy violations, destruction of data, and interruption or degradation of services.
- Do not exploit a vulnerability beyond the minimum needed to demonstrate it.
- Give us reasonable time to fix the issue before public disclosure.

## Hardening notes

Read [docs/how-to/deploy.md](../docs/how-to/deploy.md) and
[docs/reference/configuration.md](../docs/reference/configuration.md) before you deploy. At minimum:

- Replace the example values of `DJANGO_SECRET_KEY`, `DARE_INTERNAL_KEY` and `SSO_JWT_KEY` before
  any non-local deployment.
- Set `DJANGO_DEBUG=False` in production.
- Restrict `ALLOWED_HOSTS` to the hostnames you serve.
- Terminate TLS at a reverse proxy (Nginx, ALB); never expose Uvicorn directly.
- Use strong, unique passwords for the database and Redis, and enable Redis AUTH
  (`REDIS_PASSWORD`).
- Keep LLM provider keys in `.env`, never commit them, and rotate them periodically.
- Apply OS and dependency updates regularly.
