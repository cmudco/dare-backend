#import "@local/quillmark-helper:0.1.0": data
#import "@local/cmu-brand:0.1.0": (
  carnegie-red, cmu-defaults, cmu-white, cta-strip, gold-thread, red-band,
  sans-font, serif-font, stat-block,
)

#set page(
  paper: "us-letter",
  margin: (x: 0.65in, top: 0in, bottom: 0.55in),
  footer: none,
)

#show: cmu-defaults

// One-pager: everything must fit a single page — tighten body type globally.
// (Band, stats, and CTA set their own sizes explicitly.)
#set text(size: 9.5pt)
#set par(spacing: 0.7em, leading: 0.58em)

// ── Full-bleed Carnegie Red header band ──────────────────────────────────────
// Top margin is 0 and the negative pad cancels the side margins, so the band
// bleeds to the top and side edges; its inset restores the content margins.
#pad(
  x: -0.65in,
  red-band(inset: (x: 0.65in, top: 0.5in, bottom: 0.42in))[
    #text(
      font: sans-font,
      weight: "bold",
      size: 8.5pt,
      tracking: 0.24em,
    )[CARNEGIE MELLON UNIVERSITY]
    #v(10pt)
    #text(font: sans-font, weight: "bold", size: 28pt)[#data.headline]
    #{
      let s = data.at("subhead", default: "")
      if s != "" {
        v(6pt)
        text(
          font: serif-font,
          style: "italic",
          size: 12pt,
          fill: cmu-white.transparentize(10%),
        )[#s]
      }
    }
  ],
)

#v(0.28in)

// ── Stat row ─────────────────────────────────────────────────────────────────
#stat-block(data.stats)

#v(0.26in)

// ── Highlights: balance the bullet list into two columns ─────────────────────
#show list: it => {
  set par(justify: false)
  let items = it.children.map(c => if c.has("body") { c.body } else { c })
  let bullet(body) = grid(
    columns: (10pt, 1fr),
    text(fill: carnegie-red, weight: "bold")[•],
    body,
  )
  let half = calc.ceil(items.len() / 2)
  grid(
    columns: (1fr, 1fr),
    column-gutter: 18pt,
    stack(spacing: 0.8em, ..items.slice(0, half).map(bullet)),
    stack(spacing: 0.8em, ..items.slice(half).map(bullet)),
  )
}

#data.at("$body")

#v(1fr)

// ── Call to action ───────────────────────────────────────────────────────────
#{
  let cta = data.at("cta", default: "")
  if cta != "" {
    line(length: 100%, stroke: 1pt + gold-thread)
    v(8pt)
    cta-strip[#cta]
  }
}
