# Design QA — model retirement admin flow

## Evidence

- Source reference: `/var/folders/r_/1564_90n1x9gdhcg04538xs80000gn/T/TemporaryItems/NSIRD_screencaptureui_Sr70or/Screenshot 2026-08-23 at 6.16.27 AM.png`
- Implementation: `http://localhost:8000/admin/conversations/llm/36/delete/`
- Viewport: 1512 × 754, desktop Chrome, authenticated admin, dark theme
- State verified: one workflow and one agent depend on the model; Socratic integration disabled for the local fixture

## Findings and iteration history

1. The first warning action inherited the destructive red submit treatment. This made a non-destructive notification look equivalent to deletion.
2. The warning action now uses the admin primary blue treatment, while the destructive delete action remains visually separate beneath the “Delete model now” heading.
3. Browser interaction sent an advance warning for 30 August 2026 with an admin message. The success banner reported one workflow notification and one agent notification and explicitly confirmed that the model was unchanged.
4. The dependency summary, optional date, optional message, and two-stage lifecycle remain readable without introducing a modal or hidden controls.

No P0, P1, or P2 visual issues remain in the verified desktop state.

final result: passed
