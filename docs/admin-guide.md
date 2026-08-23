# DARE Backend — Administrator Guide

This guide covers operational tasks for administrators of a deployed DARE instance: user and role management, institutional configuration, and access to analytics. It assumes you have superuser access to the Django admin (`/admin/`) or shell access to the host running `manage.py`.

---

## 1. Accessing the admin

After running migrations and creating a superuser:

```bash
python manage.py createsuperuser
```

Visit `http://<host>:8000/admin/` and log in. From here you can manage users, access codes, model groups, billing, and more.

For programmatic operations, use the Django shell:

```bash
python manage.py shell
```

---

## 2. User management

### Roles

The platform uses Django's built-in user model extended with a few fields. Permission grants come from three sources:

1. **Django staff / superuser flags** — gate access to `/admin/`.
2. **Group membership** — Django auth groups; convenient for bundling permissions.
3. **Model groups** (DARE-specific) — control which LLMs a user can invoke.

### Creating users

Three options:

- **Self-service registration** — public, requires a valid access code (see §3 below).
- **Admin-created** — via `/admin/users/user/add/`. Set a temporary password and ask the user to reset.
- **Bulk import** — via management command:

  ```bash
  python manage.py shell <<EOF
  from users.models import User
  User.objects.create_user(email="alice@example.com", password="...", is_active=True)
  EOF
  ```

### Disabling / deleting users

- Set `is_active = False` to disable login (recommended over deletion — preserves their conversation history).
- Use `soft_delete()` on the model for a recoverable removal.
- Hard-delete only via admin if you need to scrub user data for compliance (note: this cascades).

### Promoting to staff / superuser

In the admin, edit the user and toggle `is_staff` (admin access) or `is_superuser` (all permissions). Or via shell:

```python
from users.models import User
u = User.objects.get(email="alice@example.com")
u.is_staff = True
u.save(update_fields=["is_staff"])
```

---

## 3. Access codes

DARE supports gated registration via access codes — useful for institutional deployments where you want to control who can sign up.

### Creating an access code group

In the admin, navigate to **Access Code Groups** and create a new group. Each group defines:

- A code (the value users enter at registration)
- An optional expiry date
- A maximum number of uses (or unlimited)
- Default model group(s) to assign to users registered via this code

### Managing codes

- **Rotating a code:** create a new group, distribute the new code, then disable the old group.
- **Viewing usage:** the admin lists each registration tied to a code.
- **Per-institution provisioning:** create one access code group per institution; users registering with that code inherit the institution's model access.

---

## 4. Model groups (LLM access control)

Each user belongs to one or more **model groups**. Each model group has a set of LLM models (e.g., `gpt-4o`, `claude-3.5-sonnet`) that members can invoke.

### Creating a model group

In `/admin/`:

1. Go to **Model Groups** → Add.
2. Name it (e.g., `Premium`, `Researchers`, `Free Tier`).
3. Select the models that group can use.
4. Save.

### Assigning users to model groups

- Edit a user → set their `model_groups`.
- Or assign automatically via access code (configure on the access code group).

### Adding a new model

When a new LLM is released:

1. Add a record in **AI Models** with:
   - Provider (`openai` / `anthropic` / `google` / `ollama`)
   - Model identifier (e.g., `gpt-5`)
   - Pricing (`input_token_rate_per_million`, `output_token_rate_per_million`)
   - Display name and description
2. Add it to the appropriate model groups.
3. Verify it appears in the user's model picker on the frontend.

---

## 5. Institutional configuration

For deployments scoped to a single institution or organization:

### Branding

The frontend reads its branding from `dare-frontend/.env`:

- `VITE_APP_NAME` — application name shown in the header
- (Logos are bundled in `src/assets/` — replace and rebuild)

### Authentication scope

The backend supports per-platform JWT scopes via the `auth_source` field on the user. Partner platforms (e.g., embedded use) can register users with a specific source, and tokens issued to that source carry that scope.

### Email templates

Outbound transactional emails (registration, password reset, confirmations) live in `users/templates/`. Override per institution by:

1. Copying the template.
2. Customizing branding and copy.
3. Setting `TEMPLATES["DIRS"]` in your settings to a custom directory that takes precedence.

### Storage

By default, file uploads land in Django's `MEDIA_ROOT`. For multi-instance deployments, mount a shared filesystem or configure a remote backend (S3, GCS) by overriding `DEFAULT_FILE_STORAGE` in production settings.

---

## 6. Analytics & reporting

### Built-in dashboards

The admin includes basic dashboards under:

- **Conversations** → message volume per user / per model
- **Billing** → token usage, cost accumulation, per-user totals
- **Files** → upload count, processing status distribution

### Token usage queries

Common query — aggregate token usage by user over a time range:

```python
from django.db.models import Sum
from billing.models import TokenUsage  # adapt to your model name

TokenUsage.objects.filter(
    created_at__gte="2026-01-01"
).values("user__email").annotate(
    total_input=Sum("input_tokens"),
    total_output=Sum("output_tokens"),
).order_by("-total_input")[:20]
```

### Exporting data

For ad-hoc analysis, export query results to CSV:

```bash
python manage.py shell <<EOF
import csv
from billing.models import TokenUsage
with open("/tmp/usage.csv", "w") as f:
    w = csv.writer(f)
    w.writerow(["user", "input_tokens", "output_tokens", "cost"])
    for u in TokenUsage.objects.all().select_related("user"):
        w.writerow([u.user.email, u.input_tokens, u.output_tokens, u.cost])
EOF
```

### Sentry

If `SENTRY_DSN` is configured, errors and performance traces flow to your Sentry project. Use Sentry's dashboards for error rates, slow endpoints, and release tracking.

---

## 7. Operational tasks

### Backups

- **Postgres:** schedule `pg_dump` via cron. Retain at least 7 days.
- **Vector store (Weaviate):** use Weaviate's snapshot API. Pinecone is managed.
- **Uploaded files:** snapshot the storage volume or, if using S3, enable versioning.

### Monitoring

Suggested signals to alert on:

- API 5xx rate (via Sentry or your APM)
- RQ queue depth (`python manage.py rqstats`)
- Redis and Postgres connection counts
- LLM provider error rate (logged at WARNING / ERROR)

### Rotating secrets

When rotating any secret in `.env`:

1. Update the value in `.env`.
2. Restart `dare-api` and `dare-worker`.
3. For `DJANGO_SECRET_KEY`: rotating invalidates existing sessions and password reset tokens — communicate before rotating.
4. For `DARE_INTERNAL_KEY`: update the value on partner backends simultaneously.

### Common shell commands

```bash
# Show queued and failed jobs
python manage.py rqstats

# Requeue all failed jobs
python manage.py rqrequeue --all-failed

# Reset a user's password
python manage.py changepassword <email>

# Show migration status
python manage.py showmigrations

# Open an interactive Python shell with Django loaded
python manage.py shell
```

---

## 8. Troubleshooting

| Issue | Where to look |
|---|---|
| User can't log in | Admin → user → check `is_active`, `is_staff`; verify email confirmed |
| Model not appearing in chooser | Verify model record exists, is in the user's model group |
| Stuck file processing | `python manage.py rqstats`; check worker logs |
| Token cost off | Verify `input_token_rate_per_million` / `output_token_rate_per_million` on the model record |
| Access code rejected | Admin → access code group → verify expiry and remaining uses |

For deeper issues, see [DEPLOYMENT.md § Troubleshooting](../DEPLOYMENT.md#troubleshooting).
