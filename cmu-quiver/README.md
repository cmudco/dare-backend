# cmu-quiver

Carnegie Mellon University brand-compliant document templates ("quills") for
the [Quillmark](https://github.com/tonguetoquill/quillmark-mcp) Typst
rendering engine. Built for the DARE platform's document-generation
integration: chat → `create_document` → typeset CMU PDF.

| Quill | Use |
|---|---|
| `cmu_letter` | Official letterhead letter — exact replica of the university's MS Word letterhead template (formal external correspondence) |
| `cmu_memo` | Official letterhead memorandum (dean, department, committee correspondence) |
| `cmu_report` | Multi-page report: branded cover, executive-summary panel, running header/footer |
| `cmu_onepager` | One-page brief: Carnegie Red headline band, stat tiles, highlights, CTA strip |

Rendered samples land in `out/` after `scripts/render-examples.sh`.

## Brand system

Everything visual comes from the shared `cmu-brand` Typst package
(`shared/cmu-brand`), which encodes the official
[CMU Brand Standards](https://brand.cmu.edu):

**Core palette** (must dominate; Carnegie Red is the star)

| Color | Hex | PMS |
|---|---|---|
| Carnegie Red | `#C41230` | 187C |
| Black | `#000000` | Black C |
| Iron Gray | `#6D6E71` | Cool Gray 10C |
| Steel Gray | `#E0E0E0` | Cool Gray 4C |
| White | `#FFFFFF` | — |

**Tartan threads** (thin accents only): Scots Rose `#EF3A47`, Gold Thread
`#FDB515`, Green Thread `#009647`, Teal Thread `#008F91`, Blue Thread
`#043673`; campus palette incl. Skibo Red `#941120`.

**Typography**: Open Sans (headings, labels, letterhead) + Source Serif 4
(body copy). Both OFL, bundled as TTFs in the package. Wordmark artwork from
the official CMU brand downloads (via tonguetoquill/typst-cmu-letter).

Components: `letterhead`, `red-rule`, `footer-bar`, `cover-page`,
`exec-summary`, `stat-block`, `red-band`, `cta-strip`, `memo-header`, plus
`cmu-defaults` (document-wide typography show-rule with branded headings,
tables, and links).

## Layout

```
Quiver.yaml                    # quiver manifest (name + description)
shared/cmu-brand/              # single-source brand package (edit HERE)
shared/assets/cmu-wordmark.svg
scripts/sync-shared.sh         # vendors shared/ into every quill (run after edits)
scripts/render-examples.sh     # renders every example.md via a running quillmark-mcp
quills/<name>/<x.y.z>/
  Quill.yaml                   # metadata + typed field schema (the LLM's API)
  plate.typ                    # Typst entry: imports @local/cmu-brand + quillmark-helper
  example.md                   # ~~~card-yaml block + realistic body (feeds get_spec)
  packages/cmu-brand/          # vendored copy (generated — do not edit)
  assets/cmu-wordmark.svg      # vendored copy (generated)
```

Quills must be self-contained, so `sync-shared.sh` **copies** the shared
package into each quill. Edit `shared/`, run the script, restart the server.

## Authoring a new quill

1. Copy an existing quill dir to `quills/<name>/0.1.0/`.
2. `Quill.yaml`: set `quill.name/version/backend/description` (the
   description tells the LLM *when to pick this template*; single line).
   Define `main.fields` with `type/default/example/ui.group/description` —
   arrays need `items: {type: string}`; there is no `required` key, so state
   "Required." in the description. Field descriptions are read by the model
   when composing documents, so write them imperative and concrete, with
   format examples ("format 'VALUE :: label'"). `main.body` accepts only
   `enabled`/`example` keys. The schema is strict — unknown keys are errors.
3. `plate.typ`: import `@local/quillmark-helper:0.1.0` (`data`; the markdown
   body is `data.at("$body")`) and `@local/cmu-brand:0.1.0`; compose with the
   brand components. Optional fields via `data.at("field", default: "")`.
4. `example.md`: a `~~~card-yaml` block whose first lines are
   `$quill: <name>@<version>` and `$kind: main`, then the fields, a closing
   `~~~`, then realistic markdown content — this is the template's showcase
   and few-shot example. `---` YAML frontmatter is not supported.
5. `scripts/sync-shared.sh && scripts/render-examples.sh` — iterate until the
   PDF is clean, then review against the brand rules.

## Rendering locally

Run quillmark-mcp with this quiver mounted (the DARE compose stack does this
automatically; standalone:)

```bash
docker run --rm -p 8090:8080 -v "$PWD:/quiver:ro" \
  -e QUILLMARK_QUIVER_DIR=/quiver quillmark-mcp:dev
scripts/render-examples.sh http://127.0.0.1:8090
open out/*.pdf
```
