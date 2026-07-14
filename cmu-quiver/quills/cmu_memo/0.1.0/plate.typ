#import "@local/quillmark-helper:0.1.0": data
#import "@local/cmu-brand:0.1.0": (
  carnegie-red, cmu-black, cmu-defaults, footer-bar, iron-gray, label-text,
  letterhead, memo-field, memo-header, red-rule, sans-font, serif-font,
)

#set page(
  paper: "us-letter",
  margin: (top: 0.75in, bottom: 1in, left: 1in, right: 1in),
  footer: footer-bar(tagline: data.at("department", default: none)),
)

#show: cmu-defaults

// ── Letterhead ───────────────────────────────────────────────────────────────
#letterhead(
  image("assets/cmu-wordmark.svg"),
  department: {
    let d = data.at("department", default: "")
    if d == "" { none } else { d }
  },
  contact: {
    let c = data.at("contact", default: ())
    if c.len() == 0 { none } else { c }
  },
)

#v(0.35in)

// ── MEMORANDUM label ─────────────────────────────────────────────────────────
#text(font: sans-font, weight: "bold", size: 13pt, fill: cmu-black, tracking: 0.18em)[MEMORANDUM]

#v(0.2in)

// ── Addressing block ─────────────────────────────────────────────────────────
#memo-header(
  memo-field("To", data.memo_to),
  memo-field("From", data.memo_from),
  memo-field(
    "Date",
    {
      let d = data.at("date", default: "")
      if d == "" { datetime.today().display("[month repr:long] [day], [year]") } else { d }
    },
  ),
  memo-field("Subject", text(weight: "bold")[#data.subject]),
  ..if data.at("cc", default: ()).len() > 0 { (memo-field("Cc", data.cc),) },
)

#v(4pt)
#red-rule(weight: 1pt)
#v(0.25in)

// ── Body ─────────────────────────────────────────────────────────────────────
#data.at("$body")

// ── Signature ────────────────────────────────────────────────────────────────
#{
  let name = data.at("signature_name", default: "")
  if name != "" {
    v(0.45in)
    set text(font: sans-font, size: 10pt)
    text(weight: "bold")[#name]
    let title = data.at("signature_title", default: "")
    if title != "" {
      linebreak()
      text(fill: iron-gray)[#title]
    }
  }
}
