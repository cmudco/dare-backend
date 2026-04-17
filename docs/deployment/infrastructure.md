# Infrastructure

> **TODO**: This page needs to be populated with production infrastructure details.

## What Needs to Be Documented

- Production server architecture (what runs where)
- nginx configuration
- SSL/TLS setup
- systemd service files for dare-backend
- Database configuration and backup procedures
- Redis configuration for production
- Environment variable management in production

## Existing Deployment Scripts

- `dare-backend/devops/deploy.sh` — Pulls latest code, runs migrations, restarts systemd service
- `dare-frontend/devops/deploy.sh` — Builds locally, deploys `dist/` to `/var/www/dare-frontend/` via SSH
- `socraticbooks-backend/docker-compose.yml` — Docker-based deployment for SocraticBooks
