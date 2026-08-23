# Local setup notes

DARE supports two local workflows. Existing contributors normally run Django
and Vite directly and put only Quillmark in Docker. A full Docker Compose stack
remains available for clean-machine onboarding.

## Existing Python + Vite workflow (recommended)

Set up and migrate the backend using the normal repository instructions, then:

```bash
scripts/quillmark-setup.sh
.venv/bin/python manage.py runserver 8000
```

Run the frontend separately:

```bash
cd ../dare-frontend
npm install
npm run dev
```

This starts only `quillmark-mcp` in Docker on loopback port 8090. Django,
Redis/Postgres, and the frontend keep using the contributor's normal workflow.
The setup script initializes the Quillmark submodule, waits for it to become
healthy, and configures DARE's `quillmark` MCP catalog row to use
`http://127.0.0.1:8090/mcp`.

To stop the renderer:

```bash
docker compose -f docker-compose.quillmark.yml down
```

## Full Docker workflow

For a fresh machine, the existing all-in-one helper remains available:

```bash
scripts/dev-setup.sh --demo-user
```

It initializes submodules, prepares local Docker overrides, starts the complete
backend stack, and optionally creates a verified funded demo account. The
frontend still runs separately with `npm run dev`.

## Connect and test

Log in, open `/mcp`, and connect **CMU Documents (Quillmark)**. Selecting that
server exposes its tools automatically to the model. The separate Documents
picker is only a convenience for choosing one of the currently advertised
templates; it does not enable tools by itself.

New users must have a verified email and wallet credit before chat requests can
run. `scripts/dev-setup.sh --demo-user` handles both for the Docker workflow.

## Deployment configuration

The MCP URL is environment-specific. After migrations, configure it explicitly:

```bash
python manage.py configure_quillmark --url http://127.0.0.1:8090/mcp
```

Run the Quillmark-only Compose project under the host's service manager so it
restarts after reboot. Keep port 8090 bound to `127.0.0.1`; the browser never
needs direct access because DARE imports rendered PDFs into its artifact store.
