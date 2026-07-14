// components.typ — CMU-branded layout components.
// Brand rules: core palette dominates; Carnegie Red is the star accent;
// tartan threads appear only as thin (≤4pt) accent strips.

#import "colors.typ": *
#import "typography.typ": *

// 2pt Carnegie Red rule — the signature letterhead stroke.
#let red-rule(weight: 2pt) = line(length: 100%, stroke: weight + carnegie-red)

#let hairline(color: steel-gray) = line(length: 100%, stroke: 0.5pt + color)

// ── Letterhead (official "lefthead": wordmark top-left, ~2.5in wide) ────────
#let letterhead(
  wordmark,
  department: none,
  contact: none, // string or array of address/contact lines
) = {
  box(width: 2.5in)[
    #set image(width: 100%)
    #wordmark
  ]
  v(2pt)
  set text(font: sans-font, size: small-size)
  if department != none {
    text(weight: "bold", fill: cmu-black)[#department]
    linebreak()
  }
  text(fill: iron-gray)[Carnegie Mellon University]
  if contact != none {
    let lines = if type(contact) == str { (contact,) } else { contact }
    for l in lines {
      linebreak()
      text(fill: iron-gray)[#l]
    }
  }
  v(6pt)
  red-rule()
}

// ── Running footer: hairline + page number + optional tagline ───────────────
#let footer-bar(tagline: none) = context {
  set text(font: sans-font, size: 7.5pt, fill: iron-gray)
  hairline()
  v(-2pt)
  grid(
    columns: (1fr, auto),
    align: (left, right),
    if tagline != none [#upper(tagline)] else [Carnegie Mellon University],
    counter(page).display("1"),
  )
}

// ── Report cover page ────────────────────────────────────────────────────────
#let cover-page(
  wordmark,
  title: "",
  subtitle: none,
  authors: (),
  department: none,
  date: none,
) = {
  page(margin: (top: 0.9in, bottom: 0.9in, left: 1in, right: 1in), footer: none, header: none)[
    #box(width: 2.7in)[
      #set image(width: 100%)
      #wordmark
    ]
    #v(4pt)
    #red-rule(weight: 3pt)
    #v(1.7in)
    #par(justify: false)[#text(font: sans-font, weight: "bold", size: 30pt, fill: carnegie-red, hyphenate: false)[#title]]
    #if subtitle != none {
      v(10pt)
      text(font: serif-font, style: "italic", size: 15pt, fill: cmu-black)[#subtitle]
    }
    #v(14pt)
    #line(length: 1.6in, stroke: 2.5pt + gold-thread) // single thread accent
    #align(bottom)[
      #set text(font: sans-font, size: 10pt)
      #if authors.len() > 0 {
        text(weight: "bold", fill: cmu-black)[#authors.join(" · ")]
        linebreak()
      }
      #if department != none {
        text(fill: iron-gray)[#department]
        linebreak()
      }
      #text(fill: iron-gray)[Carnegie Mellon University]
      #if date != none {
        linebreak()
        text(fill: iron-gray)[#date]
      }
    ]
  ]
}

// ── Executive summary panel: steel-gray field, Carnegie Red left border ─────
#let exec-summary(body) = block(
  width: 100%,
  fill: steel-gray.lighten(45%),
  stroke: (left: 3pt + carnegie-red),
  inset: (left: 14pt, right: 14pt, top: 12pt, bottom: 12pt),
  breakable: false,
)[
  #label-text[Executive Summary]
  #v(6pt)
  #set text(font: serif-font, size: 10.5pt)
  #body
]

// ── Stat tiles: value in Open Sans Bold Carnegie Red, label in iron gray ────
// stats: array of "VALUE :: label" strings.
#let stat-block(stats) = {
  let parsed = stats.map(s => {
    let parts = s.split(" :: ")
    (value: parts.at(0), label: parts.at(1, default: ""))
  })
  grid(
    columns: (1fr,) * parsed.len(),
    column-gutter: 12pt,
    ..parsed.map(s => block(
      width: 100%,
      stroke: (top: 3pt + carnegie-red),
      inset: (top: 8pt, bottom: 4pt),
    )[
      #text(font: sans-font, weight: "bold", size: 30pt, fill: carnegie-red)[#s.value]
      #v(2pt)
      #label-text[#s.label]
    ]),
  )
}

// ── Full-width Carnegie Red band with reversed (white) content ──────────────
#let red-band(inset: 0.55in, body) = block(
  width: 100%,
  fill: carnegie-red,
  inset: inset,
)[
  #set text(fill: cmu-white)
  #body
]

// ── Call-to-action strip ─────────────────────────────────────────────────────
#let cta-strip(body) = block(
  width: 100%,
  fill: cmu-black,
  inset: (x: 16pt, y: 12pt),
)[
  #set text(font: sans-font, fill: cmu-white, size: 10pt, weight: "bold")
  #body
]

// ── Memo header grid (TO / FROM / DATE / SUBJECT) ────────────────────────────
#let memo-field(label, value) = (
  label-text[#label],
  {
    set text(font: sans-font, size: 10pt, fill: cmu-black)
    if type(value) == array { value.join([#linebreak()]) } else { value }
  },
)

#let memo-header(..fields) = {
  grid(
    columns: (0.9in, 1fr),
    row-gutter: 9pt,
    ..fields.pos().flatten(),
  )
}
