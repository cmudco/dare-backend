# Deployment Procedures

## dare-backend

Script: `dare-backend/devops/deploy.sh`

Use this sequence for a standard production update:

1. Announce the deployment window if users may be affected.
2. Pull the target commit or release tag.
3. Activate the virtual environment.
4. Install dependencies: `pip install -r requirements/prod.txt`.
5. Run checks: `python manage.py check`.
6. Run migrations: `python manage.py migrate`.
7. Collect static files: `python manage.py collectstatic --noinput`.
8. Restart the API and worker services.
9. Verify health endpoints: `/api/health/` and `/api/ready/`.

If a change includes database migrations, deploy the backend before any frontend that depends on the new API shape.

## dare-frontend

Script: `dare-frontend/devops/deploy.sh`

1. Set production `VITE_*` values before building.
2. Build: `npm run build`.
3. Upload `dist/` to the static host.
4. Confirm the static host rewrites SPA routes to `index.html`.
5. Confirm REST and Socket.IO requests reach the configured backend.

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

## Rollback

For backend-only failures:

1. Revert to the previous known-good commit or release tag.
2. Reinstall dependencies if the lockset changed.
3. Restart the API and worker services.
4. Verify health endpoints and key user flows.

Database rollbacks are migration-specific. Prefer forward-fix migrations unless the migration was explicitly designed to reverse safely.
