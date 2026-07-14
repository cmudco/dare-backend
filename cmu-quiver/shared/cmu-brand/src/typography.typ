// typography.typ — CMU Brand Standards typography.
// Primary sans: Open Sans (labels, headings, UI-like elements).
// Serif: Source Serif 4 (long-form body copy, "heightened sophistication").
// Fallbacks per the standards: Helvetica / Times.

#import "colors.typ": *

#let sans-font = ("Open Sans", "Helvetica")
#let serif-font = ("Source Serif 4", "Times New Roman")

#let body-size = 10.5pt
#let small-size = 8pt

// Document-wide defaults: serif body with sans headings in the brand scale.
// H1 Open Sans Bold Carnegie Red · H2 black · H3 iron-gray small caps.
#let cmu-defaults(body) = {
  set text(font: serif-font, size: body-size, fill: cmu-black)
  set par(justify: true, spacing: 0.9em, leading: 0.62em)

  show heading.where(level: 1): it => block(above: 1.6em, below: 0.8em)[
    #text(font: sans-font, weight: "bold", size: 15pt, fill: carnegie-red)[#it.body]
  ]
  show heading.where(level: 2): it => block(above: 1.3em, below: 0.6em)[
    #text(font: sans-font, weight: "bold", size: 12pt, fill: cmu-black)[#it.body]
  ]
  show heading.where(level: 3): it => block(above: 1.1em, below: 0.5em)[
    #text(font: sans-font, weight: "semibold", size: 10pt, fill: iron-gray, tracking: 0.06em)[#upper(it.body)]
  ]

  show link: set text(fill: skibo-red)
  show strong: set text(weight: "bold")

  // Branded tables: hairline steel-gray grid under a Carnegie Red top rule,
  // sans header-ish first row via table cell defaults kept subtle.
  set table(
    stroke: 0.5pt + steel-gray.darken(10%),
    inset: (x: 8pt, y: 6pt),
  )
  show table: it => block(stroke: (top: 1.5pt + carnegie-red), width: auto)[#it]

  body
}

// Small utility styles
#let label-text(content) = par(justify: false)[#text(
  font: sans-font,
  size: small-size,
  weight: "bold",
  fill: iron-gray,
  tracking: 0.08em,
  hyphenate: false,
)[#upper(content)]]

#let sans(content, ..args) = text(font: sans-font, ..args)[#content]
