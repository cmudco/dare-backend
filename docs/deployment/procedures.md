# Deployment Procedures

> **TODO**: This page needs to be expanded with detailed step-by-step deployment procedures.

## dare-backend

Script: `dare-backend/devops/deploy.sh`

1. SSH into production server
2. Pull latest code from git
3. Activate virtual environment
4. Install new dependencies: `pip install -r requirements/prod.txt`
5. Run migrations: `python manage.py migrate`
6. Collect static files: `python manage.py collectstatic --noinput`
7. Restart systemd service: `sudo systemctl restart dare`

## dare-frontend

Script: `dare-frontend/devops/deploy.sh`

1. Build locally: `npm run build`
2. Upload `dist/` to production server via SSH/SCP
3. Files served by nginx from `/var/www/dare-frontend/`

## socraticbooks-backend

Uses Docker Compose for deployment:

```bash
docker compose up -d --build
docker compose exec web python /app/backend/manage.py migrate
```

## Deployment Order

When deploying changes that span multiple projects, deploy in this order:

1. **dare-backend** (other services depend on it)
2. **socraticbooks-backend** (depends on dare-backend API)
3. **dare-frontend** (depends on dare-backend API)
4. **socraticbooks-react** (depends on socraticbooks-backend API)

If there are breaking API changes, coordinate backend and frontend deployments to minimize downtime.
