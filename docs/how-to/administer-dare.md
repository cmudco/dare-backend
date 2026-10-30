# Administer DARE

Run a deployed DARE instance day to day: users and roles, access codes, model access, institutional settings, analytics and routine operations. You need superuser access to the Django admin (`/admin/`) or shell access to the host that runs `manage.py`.

## 1. Accessing the admin

After running migrations and creating a superuser:

```bash
python manage.py createsuperuser
```

Open `http://<host>:8000/admin/` and log in to manage users, access codes, model groups, billing and more.

For programmatic operations, use the Django shell:

```bash
python manage.py shell
```

## 2. User management

### Roles

The user model extends Django's built-in user with a few fields. Permissions come from three sources:

1. **Django staff / superuser flags.** Gate access to `/admin/`.
2. **Group membership.** Django auth groups bundle permissions.
3. **Model groups** (DARE-specific). Control which LLMs a user can call.

### Creating users

Three options:

- **Self-service registration.** Public; needs a valid access code (see §3).
- **Admin-created.** Use `/admin/users/user/add/`. Set a temporary password and ask the user to reset it.
- **Bulk import.** Use the Django shell:

  ```bash
  python manage.py shell <<EOF
  from users.models import User
  User.objects.create_user(email="alice@example.com", password="...", is_active=True)
  EOF
  ```

### Disabling / deleting users

- Set `is_active = False` to disable login. Prefer this over deletion: it keeps their conversation history.
- Call `soft_delete()` on the user for a recoverable removal (it sets `is_deleted`; `undelete()` reverses it).
- Hard-delete in the admin only when you must scrub user data for compliance. It cascades.

### Promoting to staff / superuser

In the admin, edit the user and toggle `is_staff` (admin access) or `is_superuser` (all permissions). Or use the shell:

```python
from users.models import User
u = User.objects.get(email="alice@example.com")
u.is_staff = True
u.save(update_fields=["is_staff"])
```

## 3. Access codes

Registration needs an access code, so you control who can sign up.

### Creating an access code group

In the admin, open **Access code groups** and add a group. Each group defines:

- **Access code.** The value users enter at registration (unique).
- **Max capacity.** How many users can register with it; `current_usage` counts them.
- **Expiration date** (optional). Users with this code are deactivated after it.
- **Default role**, **model group** and optional **initial wallet credit** for users who register with it.
- **Is active**, and internal **notes** (class, semester, purpose).

### Managing codes

- **Rotate a code.** Create a new group, distribute the new code, then untick **Is active** on the old group.
- **See usage.** Filter the user list by access code group.
- **Provision per institution.** Create one access code group per institution; users who register with that code get the institution's model access.

## 4. Model groups (LLM access control)

A user's models come from the **model group** on their access code group. Each model group has a set of allowed LLMs. A user whose access code group has no model group (or an inactive one) can use every active model.

### Creating a model group

In `/admin/`:

1. Go to **Model Groups** → Add.
2. Name it (for example `Premium`, `Researchers`, `Free Tier`).
3. Select the models that group can use.
4. Save.

### Assigning users to model groups

- Set **Model group** on the access code group; everyone registered with that code gets it.
- To move one user, change their access code group.

### Adding a new model

When a new LLM is released:

1. Add a record in **LLMs** with:
   - Provider (`openai` / `claude` / `gemini` / `llama` / `custom`)
   - Model identifier (for example `gpt-5`)
   - Pricing (`input_token_rate_per_million`, `output_token_rate_per_million`)
   - Display name and description
2. Add it to the right model groups.
3. Check it appears in the user's model picker on the frontend.

## 5. Institutional configuration

For deployments scoped to a single institution or organization:

### Branding

Branding is part of the frontend build: logos and icons live in `dare-frontend/public/` and the
source. Replace them and rebuild. The frontend `.env` has no branding variables.

### Authentication scope

Each user has an `auth_source` (`DARE` or `SocraticBots`): the platform where they first signed up. Partner platforms register users with their own source, and tokens issued to that source carry that scope.

### Email templates

Transactional emails (registration, password reset, confirmations) use django-allauth's `account/email/*` templates; `users/adapter.py` adds the frontend links. To override them per institution:

1. Create a `templates/` directory at the repo root (`TEMPLATES["DIRS"]` in `config/settings/common.py` already points there).
2. Copy the allauth template you want to change into `templates/account/email/`.
3. Edit the branding and copy.

### Storage

File uploads land in Django's `MEDIA_ROOT` (`<repo>/media`). Each user's `storage_backend` picks local filesystem (the default) or SyftBox for new uploads. For multi-instance deployments, mount a shared filesystem at `MEDIA_ROOT`.

## 6. Analytics & reporting

### Built-in dashboards

The admin has two read-only dashboards under **Billing**:

- **Usage Dashboard.** Platform-wide usage and cost, by period, from transactions, messages and wallets.
- **Usage Breakdown.** Cost per user and per group.

The **Conversations** and **Files** admin lists show individual messages and uploads.

### Token usage queries

To total token usage by user over a time range, query transactions. Billed calls are `billing.models.Transaction` rows with `input_tokens`, `output_tokens` and `amount`:

```python
from django.db.models import Sum
from billing.models import Transaction

Transaction.objects.filter(
    created_at__gte="2026-01-01"
).values("user__email").annotate(
    total_input=Sum("input_tokens"),
    total_output=Sum("output_tokens"),
).order_by("-total_input")[:20]
```

### Exporting data

For ad-hoc analysis, export to CSV. Users can also download their own history from `GET /api/billing/transactions/export/` (see [REST API](../reference/rest-api.md)).

```bash
python manage.py shell <<EOF
import csv
from billing.models import Transaction
with open("/tmp/usage.csv", "w") as f:
    w = csv.writer(f)
    w.writerow(["user", "input_tokens", "output_tokens", "amount"])
    for t in Transaction.objects.all().select_related("user"):
        w.writerow([t.user.email, t.input_tokens, t.output_tokens, t.amount])
EOF
```

### Sentry

With `SENTRY_DSN` set, errors and performance traces go to your Sentry project. Use Sentry for error rates, slow endpoints and release tracking.

## 7. Operational tasks

### Backups

- **Postgres:** schedule `pg_dump` via cron. Retain at least 7 days.
- **Vector store (Weaviate):** use Weaviate's snapshot API. Pinecone is managed.
- **Uploaded files:** snapshot the storage volume or, if using S3, enable versioning.

### Monitoring

Alert on:

- API 5xx rate (via Sentry or your APM)
- RQ queue depth (`python manage.py rqstats`)
- Redis and Postgres connection counts
- LLM provider error rate (logged at WARNING / ERROR)

### Rotating secrets

When rotating any secret in `.env`:

1. Update the value in `.env`.
2. Restart `dare-api` and `dare-worker`.
3. For `DJANGO_SECRET_KEY`: rotating invalidates existing sessions and password reset tokens, so tell users first.
4. For `DARE_INTERNAL_KEY`: update the value on partner backends at the same time.

### Common shell commands

```bash
# Show queued and failed jobs
python manage.py rqstats

# Requeue all failed jobs on a queue (rq CLI)
rq requeue --all --queue default --url redis://localhost:6379

# Reset a user's password
python manage.py changepassword <email>

# Show migration status
python manage.py showmigrations

# Open an interactive Python shell with Django loaded
python manage.py shell
```

## 8. Troubleshooting

| Issue | Where to look |
|---|---|
| User can't log in | Admin → user → check `is_active`, `is_staff`; verify email confirmed |
| Model not appearing in chooser | Check the model record exists, is active, and is in the user's model group |
| Stuck file processing | `python manage.py rqstats`; check worker logs |
| Token cost off | Verify `input_token_rate_per_million` / `output_token_rate_per_million` on the model record |
| Access code rejected | Admin → access code group → check **Is active**, expiry, and `current_usage` against max capacity |

For deeper issues, see [Deploy § Troubleshooting](deploy.md#troubleshooting).
