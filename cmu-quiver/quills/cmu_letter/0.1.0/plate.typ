// cmu_letter — replica of the official CMU MS Word letterhead template
// (cmu-msword-letterhead-lefthead.docx, "lefthead" variant).
//
// Every metric below is taken from that template's OOXML. Source values are
// noted in comments as twips (1440/in), EMU (914400/in), or half-points.
// Word's "single" line spacing for Open Sans is 1.3618em (hhea 2189/-600/2048),
// so 9pt body lines sit on a 12.26pt pitch; Typst's default text edges are
// cap-height/baseline, hence leading = pitch - cap-height (0.7139em).

#import "@local/quillmark-helper:0.1.0": data
#import "@local/cmu-brand:0.1.0": cmu-black, sans-font

// ── Field access ─────────────────────────────────────────────────────────────
#let department = data.at("department", default: "")
#let address = {
  let a = data.at("address", default: ())
  if a.len() == 0 {
    ("Carnegie Mellon University", "5000 Forbes Avenue", "Pittsburgh, PA 15213-3890")
  } else { a }
}
#let website = {
  let w = data.at("website", default: "")
  if w == "" { "cmu.edu" } else { w }
}
#let date-line = {
  let d = data.at("date", default: "")
  if d == "" { datetime.today().display("[month repr:long] [day], [year]") } else { d }
}
#let closing = {
  let c = data.at("closing", default: "")
  if c == "" { "Sincerely," } else { c }
}

// ── Letterhead (Word default header — repeats on every page) ─────────────────
// Wordmark: CMU_Logo_Horiz_Red anchored to the page at (1141730, 427355) EMU
// = (1.2485in, 0.4673in), displayed 2281555 EMU = 2.4946in wide.
// Address block: flows at the 1.25in left margin; first line ("Department
// Here", Open Sans Bold 7pt) caps at 58.9pt from the page top (0.5in header
// offset + two blank 7.5pt lines + baseline placement in the 9.5pt exact
// line box). Lines sit on an exact 9.5pt pitch (w:line="190" lineRule="exact")
// with a 7pt blank line (w:line="140") before the web address.
#let letterhead = {
  place(top + left, dx: 1.2485in, dy: 0.4673in, image("assets/cmu-wordmark.svg", width: 2.4946in))
  place(top + left, dx: 1.25in, dy: 0.818in, {
    set text(font: sans-font, size: 7pt, fill: cmu-black) // w:sz 14 half-pt
    set par(leading: 4.5pt, spacing: 11.5pt) // 9.5pt pitch; 16.5pt across the gap
    par({
      if department != "" {
        text(weight: "bold")[#department]
        linebreak()
      }
      address.join([#linebreak()])
    })
    par(website)
  })
}

// ── Page & body typography ────────────────────────────────────────────────────
// pgMar: top 3960 / right 1800 / bottom 1440 / left 1800 twips.
#set page(
  paper: "us-letter",
  margin: (top: 2.75in, bottom: 1in, left: 1.25in, right: 1.25in),
  background: letterhead,
)

// Body runs: Open Sans 9pt (w:sz 18 half-pt), auto/black, left-aligned,
// single-spaced. The template separates paragraphs with one blank line
// (24.51pt baseline pitch), so paragraph spacing = 24.51 - 6.43 cap = 18.1pt.
#set text(font: sans-font, size: 9pt, fill: cmu-black, hyphenate: false)
#set par(justify: false, leading: 5.83pt, spacing: 18.1pt)

// A gap of n blank 9pt lines between blocks (block bottom edge = baseline,
// next block top edge = cap-height): (n + 1) * 12.26pt pitch - 6.43pt cap.
#let blank-lines(n) = v((n + 1) * 12.26pt - 6.43pt, weak: true)

// ── Letter body (mirrors the template's copy structure exactly) ──────────────
// Date sits directly above the recipient block, no blank line between.
#par({
  date-line
  linebreak()
  data.recipient.join([#linebreak()])
})

#blank-lines(3)

#par(data.salutation)

#data.at("$body")

#par(closing)

// Three blank lines of wet-signature space, per the template.
#blank-lines(3)

#par({
  data.signature_name
  let title = data.at("signature_title", default: "")
  if title != "" {
    linebreak()
    title
  }
})
