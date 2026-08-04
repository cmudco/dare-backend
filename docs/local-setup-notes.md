# Local Setup Notes (ttq-spike)

Field notes from standing this stack up from a fresh clone (July 2026). Complements
[INSTALL.md](../INSTALL.md).

## 0. The short path

```bash
git clone --recurse-submodules https://github.com/tonguetoquill/dare-backend.git
cd dare-backend
scripts/dev-setup.sh --demo-user
```

The script checks Docker, inits the `quillmark-mcp` submodule, writes `.env`
+ `docker-compose.override.yml`, starts the stack (migrations run in the
entrypoint), and creates a verified `demo@dare.local` / `darelocal` superuser
with $100 wallet credit. Set `CLAUDE_API_KEY` in `docker-compose.override.yml`
afterwards and `docker compose up -d`.

Frontend: `cd ../dare-frontend && npm install && npm run dev` → http://localhost:5173.

Document generation: log in, visit `/mcp`, click **Connect** on *CMU Documents*,
then chat — ask for a memo and the assistant returns a rendered PDF.

Everything below documents what the script automates, plus gotchas if you go manual.

## 1. `DB_PASSWORD` is required

`.example.env` ships without a value, and `docker-compose.yml` uses
`${DB_PASSWORD:?set_DB_PASSWORD}` — every `docker compose` command fails until you set
it in `.env` (e.g. `DB_PASSWORD=darelocal`). `dev-setup.sh` appends this for you.

## 2. Secrets via `docker-compose.override.yml`

Compose merges `docker-compose.override.yml` automatically, and it is gitignored —
a convenient place for local secrets instead of editing `.env`. `dev-setup.sh`
generates one with a fresh `DJANGO_SECRET_KEY` and an empty `CLAUDE_API_KEY` slot,
and mounts `.:/app` into web + worker for hot reload.

Provider env var names are `CLAUDE_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`
(see `config/env.py` — the README's `ANTHROPIC_API_KEY`/`GOOGLE_API_KEY` mentions
are stale).

## 3. Superuser login requires a verified email

`createsuperuser` alone cannot log in through the frontend — django-allauth rejects
unverified emails and there is no local mail server. `dev-setup.sh --demo-user`
handles this; manually:

```bash
docker compose exec web python manage.py shell -c "
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
u = get_user_model().objects.get(email='<your email>')
EmailAddress.objects.update_or_create(user=u, email=u.email, defaults={'verified': True, 'primary': True})
"
```

Note the custom `User` model has no `username` field — look users up by `email`.

## 4. Model catalog

Migration `conversations/0078_fix_claude_model_catalog` deactivates Claude model IDs
that Anthropic has retired and fixes zero-cost token rates. If you only configure one
provider key, consider deactivating the other providers' rows so the picker only
offers models that work:

```bash
docker compose exec web python manage.py shell -c "
from conversations.models import LLM
LLM.objects.filter(provider__in=['openai','gemini']).update(is_active=False)
"
```

(Leaving them active with no key breaks chat sends and conversation titles.)

## 5. Wallet credit

New users start at \$0.00 and billing pre-checks block chat. `dev-setup.sh
--demo-user` tops up the demo user; manually:

```bash
docker compose exec web python manage.py shell -c "
from decimal import Decimal
from django.contrib.auth import get_user_model
from billing.services import WalletService
u = get_user_model().objects.get(email='<your email>')
WalletService.add_topup(u, amount=Decimal('100.00'), message='local dev top-up')
"
```

## 6. Document generation (quillmark-mcp + cmu-quiver)

- `quillmark-mcp/` is a git submodule (https://github.com/tonguetoquill/quillmark-mcp)
  and the build context for the `quillmark-mcp` compose service. If the directory is
  empty, run `git submodule update --init`.
- The quill templates live in the in-repo `cmu-quiver/` directory, mounted
  read-only at `/quiver`. See `cmu-quiver/README.md` for the brand system and
  quill-authoring guide.
- The DARE backend reaches quillmark over the compose network; host port
  `127.0.0.1:8090` is debug-only.

## 7. Health checks

```bash
curl http://localhost:8000/api/health/   # liveness
curl http://localhost:8000/api/ready/    # DB + Redis
```
