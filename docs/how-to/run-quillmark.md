# Run the Quillmark document renderer

Run Quillmark, the CMU PDF renderer, next to a local DARE so chat can generate documents. There
are two ways:

- **Python + Vite (recommended).** Run Django and Vite as usual and put only Quillmark in Docker.
- **Full Docker.** One script brings up the whole backend stack on a fresh machine.

## Python + Vite workflow (recommended)

Set up and migrate the backend as in the [quick start](../tutorials/quickstart.md), then:

```bash
scripts/quillmark-setup.sh
.venv/bin/uvicorn dare.asgi:application --port 8000 --reload
```

Run the frontend separately:

```bash
cd ../dare-frontend
npm install
npm run dev
```

The setup script starts only `quillmark-mcp` in Docker on loopback port 8090. Django,
Redis/Postgres and the frontend run as usual. The script initializes the Quillmark submodule,
waits for it to become healthy, and points DARE's `quillmark` MCP catalog row at
`http://127.0.0.1:8090/mcp`.

To stop the renderer:

```bash
docker compose -f docker-compose.quillmark.yml down
```

## Full Docker workflow

On a fresh machine, use the all-in-one helper:

```bash
scripts/dev-setup.sh --demo-user
```

It initializes submodules, prepares local Docker overrides, starts the full backend stack, and
with `--demo-user` creates a verified, funded demo account. Run the frontend separately with
`npm run dev`.

## Connect and test

Log in, open `/mcp`, and connect **CMU Documents (Quillmark)**. Selecting that server exposes its
tools to the model. The Documents picker only helps you choose one of the advertised templates;
it does not enable tools by itself.

New users need a verified email and wallet credit before chat requests run. `scripts/dev-setup.sh --demo-user` handles both for the Docker workflow.

## Deployment configuration

The MCP URL is environment-specific. After migrations, configure it explicitly:

```bash
python manage.py configure_quillmark --url http://127.0.0.1:8090/mcp
```

Run the Quillmark-only Compose project under the host's service manager so it
restarts after reboot. Keep port 8090 bound to `127.0.0.1`; the browser never
needs direct access because DARE imports rendered PDFs into its artifact store.
