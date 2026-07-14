#import "@local/quillmark-helper:0.1.0": data
#import "@local/cmu-brand:0.1.0": (
  cmu-defaults, cover-page, exec-summary, footer-bar, hairline, iron-gray,
  sans-font,
)

// ── Shared values ────────────────────────────────────────────────────────────
#let report-title = data.title
#let department = {
  let d = data.at("department", default: "")
  if d == "" { none } else { d }
}
#let report-date = {
  let d = data.at("date", default: "")
  if d == "" { datetime.today().display("[month repr:long] [day], [year]") } else { d }
}

#set page(paper: "us-letter")
#show: cmu-defaults

// ── Cover page (renders its own page: no running header or footer) ──────────
#cover-page(
  image("assets/cmu-wordmark.svg"),
  title: report-title,
  subtitle: {
    let s = data.at("subtitle", default: "")
    if s == "" { none } else { s }
  },
  authors: data.author,
  department: department,
  date: report-date,
)

// ── Content pages: running header + footer from here on ─────────────────────
#set page(
  margin: 1in,
  header: context {
    set text(font: sans-font, size: 7.5pt, fill: iron-gray, tracking: 0.06em)
    align(right)[#upper(report-title)]
    v(-4pt)
    hairline()
  },
  footer: footer-bar(tagline: department),
)

// ── Executive summary ────────────────────────────────────────────────────────
#{
  let summary = data.at("exec_summary", default: "")
  if summary != "" {
    exec-summary[#summary]
    v(0.2in)
  }
}

// ── Body ─────────────────────────────────────────────────────────────────────
#data.at("$body")
