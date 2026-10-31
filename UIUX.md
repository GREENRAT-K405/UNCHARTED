# UI/UX

## UI polish pass

A visual-only pass on branch `ui-polish`: three stylesheets (`styles.css`, `styles-call.css`, `styles-insights.css`) and the font link in `index.html`. No JSX, routes, props, state, API calls or backend code changed, so every button, section and field is exactly where it was.

### Art direction: "warm kitchen light"

Gentle, familiar and human, not clinical and not a SaaS template.

| | Decision |
|---|---|
| Ground | Oat `#f6f2ea`, with `#fffdf8` surfaces. Surfaces are separated by a step in tone, not by hairlines. |
| Ink | Warm charcoal `#231f1a` (14.7:1 on the ground). The secondary and muted greys were darkened so they all pass AA. |
| Brand | One forest green `#1b5446`, with a deeper `#143f35` for hover and press. |
| Accent | One marigold `#e9b949`, used only for "Who is this?", recall moments and the listening mic. It replaces the old terracotta. |
| Type | Atkinson Hyperlegible (built for low vision) does the work. Fraunces (a soft serif) is used only for greetings, names and headline moments. Uppercase tracked labels became sentence case. Monospace is gone from the UI. |
| Shape | Radii 12 / 20 / 28 / pill. Two elevations: a soft one at rest and a float shadow for dialogs and overlay cards. |
| Space | 4px base scale as tokens (`--s-1` to `--s-6`). |
| Motion | 120 to 220ms, a soft ease-out, transform and opacity only. Press, hover and card-lift, overlay fade and slide. Global `prefers-reduced-motion` fallback. No new dependencies. |
| Targets | Patient and relative controls are at least 48px, the patient's primary actions 60 to 104px, caregiver controls at least 36 to 42px. |
| Focus | One visible `:focus-visible` ring everywhere (4px on patient and relative screens). |

All colours, radii, spacing and motion values are CSS variables on `:root`, shared by every screen.

### Before / after by screen

| Screen | What changed |
|---|---|
| **Patient home** | Greeting and names in Fraunces. "Join the family call" is now the one dominant action: "Who do you want to talk to?" became a soft secondary button. Tiles have real hover, focus and press states and a lighter shadow. Help pill no longer wraps into a blob on phones and no longer overlaps the family-menu corner. |
| **Patient call** | "Who is this?" card: Fraunces name, the memory line in a marigold block, a float shadow, no outline. Controls are 52 to 60px with press states, and the flat marigold button replaces the glowing one. The About panel is calm blocks with sentence-case headings instead of ruled uppercase sections. The waiting screen is a flat surface instead of a gradient. |
| **Relative screens** | `/c/…`, `/family` and the consent page: Fraunces headline, tighter vertical rhythm, a muted "keep this link" card, 48px buttons, a bigger update box. Ring screen: flat green, larger Accept and Decline (68px), Accept darkened to 5.6:1. Call panel: grouped soft blocks, sentence-case headings, 40px thumbs. |
| **Landing** | Three even cards in a row, a larger Fraunces title, clearer card hover and focus. |
| **Sign-in** | 48px fields, 2px borders, a calmer dev-mode notice, a Fraunces side headline. |
| **Patient pairing** | Fraunces "Hello!", the pairing code in Atkinson digits (monospace removed), a larger help text. |
| **Caregiver** | Panels and stat tiles on tone steps instead of outlined boxes, a clean sticky top bar (static on phones), a brand-coloured range selector, sentence-case labels, larger secondary text, hover states on rows, a stronger nav item height. Charts keep their validated colour pair. |

### Checks

- 0 console errors on `/`, `/login`, `/patient`, `/family`, `/c/…` and `/caregiver`.
- Contrast: ink on oat 14.7:1, muted ink 5.4:1, white on brand 8.7:1, accent ink on marigold 7.6:1, Help button 5.9:1, Decline 6.3:1, Accept 5.6:1.
- Screens were reviewed at 1440px and 390px before and after. The live two-sided call was reviewed at 1440px only.
- Reviewed against the Web Interface Guidelines: no `transition: all`, no `outline: none` without a focus replacement, `touch-action`, `overscroll-behavior` and safe-area insets added, `color-scheme` set, balanced heading wrapping.

## Suggestions (not implemented)

These need markup, behaviour or content changes, so they were left out of a visual-only pass.

1. **Form labels and names.** A few caregiver inputs use only a placeholder. Add `<label>` or `aria-label`, `name` and `autocomplete`, and `spellCheck={false}` on codes and emails.
2. **Icon-only buttons.** Give the thumbs up and down, the family-menu corner and similar controls explicit `aria-label`s, and mark decorative icons `aria-hidden`.
3. **Skip link and landmarks.** Add a "skip to main content" link and `<main>` landmarks on the caregiver shell.
4. **Live regions.** Mark the "Updated just now" note, the unanswered-call banner and form errors `aria-live="polite"`.
5. **Caregiver tabs in the URL.** The current tab is kept in local storage. Putting it in the URL would make tabs linkable and the back button work.
6. **Patient "Who do you want to talk to?" button.** On a phone it wraps to two lines. A shorter label would fit one line.
7. **Pairing code.** Split the code into two groups of three for easier reading and for a screen reader.
8. **Dark theme for patient screens.** The call stage is dark by design, but the rest is light only. A true evening mode for the patient home would be gentler at night.
9. **Contact avatar colours.** The per-person colours are strong and saturated. A softer, tonally matched set would sit better with the new palette.
10. **Truncation of very long names and notes** on contact tiles and the caregiver contact cards.
11. **Loading and empty states.** Some screens show a bare "Loading…" or an empty box. Add skeleton rows and a short, kind empty message.
12. **Real photography.** Contact tiles fall back to a coloured initial. Prompting caregivers to add a photo during onboarding would help recognition most.
