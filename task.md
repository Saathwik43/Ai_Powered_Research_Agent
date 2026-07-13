
Repo: Saathwik43/Ai_Powered_Research_Agent
Files: NEW frontend/src/pages/Dashboard.css, NEW frontend/src/pages/AdminDashboard.css, NEW frontend/src/pages/VenueRecommendations.css, frontend/src/pages/LandingPage.css

Goal: bring Dashboard, AdminDashboard, VenueRecommendations up to the same design bar as PdfAnalysis/ManuscriptBuilder/LiteratureSurvey (already polished this session). Reuse the exact same design language already established — don't invent new patterns.

Established design language to match (audit these 3 files first for reference before writing new CSS):

- Card entrance: @keyframes cardIn (opacity+translateY(8px), 0.35s ease-out, staggered ~40ms per item)
- Buttons: :active { transform: scale(0.97) }
- Drawer/panel transitions: cubic-bezier(0.32,0.72,0,1), 0.25-0.28s
- Skeleton loaders (shimmer gradient) instead of bare spinners for data-loading states
- Step/tab active states: left accent border (3px solid var(--primary)) + var(--primary-light) background
- All colors via existing CSS vars in index.css (--primary, --bg-card, --border, --text-muted etc) — zero hardcoded hex

1. Dashboard.jsx/css: add className hooks to "Recent Surveys"/"Popular Venues" grid cards, apply cardIn stagger animation, skeleton loaders for initial data fetch, confirm grid-cols-* classes already collapse responsively (check index.css @768px rule still applies).
2. AdminDashboard.jsx/css: audit current inline styles first (this page has zero polish applied all session — check table/list rendering, button styles, any data tables need hover states + zebra striping using var(--bg-elevated)), add responsive @media 768px stacking for any multi-column layouts, add the same button press-feedback globally if not already inherited from index.css's .btn:active rule.
3. VenueRecommendations.jsx/css: same audit — likely a results-list page similar to LiteratureSurvey's card pattern, apply identical .lit-result-card-style animation/hover/responsive treatment (copy the pattern, don't reinvent).
4. LandingPage.css: refresh hero section motion — add entrance animation on load (fade+slight scale on hero title/CTA), ensure any feature-cards use the same cardIn stagger pattern for consistency with interior pages.
5. Site-wide check: grep all page CSS files for any remaining hardcoded hex colors not using var() — replace with matching token from index.css :root list.

Do not touch: backend, API calls, routing, auth logic. CSS/animation/responsive only, additive classNames alongside existing inline styles.
Test at 375px and 1440px for each page — zero horizontal scroll, animations feel consistent site-wide, not page-by-page different.
