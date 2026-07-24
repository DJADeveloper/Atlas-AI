# Accessibility Commitments

## Standard

The product targets WCAG 2.2 level AA. Every new screen ships with keyboard
navigation, visible focus states, and screen-reader labels reviewed against
the component checklist.

## Testing

Automated axe scans run in CI on every pull request that touches the web
app; a manual assistive-technology pass with a screen reader happens before
each quarterly release.

## Known gaps

The legacy analytics dashboard fails contrast requirements in dark mode and
is scheduled for replacement in the design-system migration this year.
