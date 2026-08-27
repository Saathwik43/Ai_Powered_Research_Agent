/** Canonical public origin used in sitemap.xml. Keep in sync with that file. */
export const SITE_ORIGIN = 'https://ai-powered-research-agent-live.onrender.com';
export const SITE_NAME = 'Research Agent';

/**
 * Unique tab title and meta description per public/app route (LCH-3, LCH-4).
 * Unknown paths fall back to the 404 entry in PageMeta.
 */
export const PAGE_META = {
  '/': {
    title: 'Research Agent — Discover topics, draft papers, and match venues',
    description:
      'One workspace for academic research: discover topics, survey literature, draft manuscripts, and match journals and conferences without switching tools.',
  },
  '/login': {
    title: 'Sign in · Research Agent',
    description:
      'Sign in to your Research Agent workspace to continue topic discovery, literature review, manuscript drafts, and venue matching.',
  },
  '/signup': {
    title: 'Create an account · Research Agent',
    description:
      'Create a free Research Agent account to discover research topics, survey papers, draft manuscripts, and find publication venues.',
  },
  '/signup/complete': {
    title: 'Check your email · Research Agent',
    description:
      'Confirm the address we just emailed you to activate your Research Agent account. The link expires in 24 hours.',
  },
  '/forgot-password': {
    title: 'Reset your password · Research Agent',
    description:
      'Request a password reset link for your Research Agent account. We email instructions if that address is registered.',
  },
  '/reset-password': {
    title: 'Choose a new password · Research Agent',
    description:
      'Set a new password for your Research Agent account using the reset link sent to your email.',
  },
  '/verify-email': {
    title: 'Confirm your email · Research Agent',
    description:
      'Confirm the email address on your Research Agent account to activate it and open your workspace.',
  },
  '/dashboard': {
    title: 'Research Discovery · Research Agent',
    description:
      'Discover research topics, compare impact, and start a literature survey from your Research Agent workspace.',
  },
  '/literature-survey': {
    title: 'Literature Survey · Research Agent',
    description:
      'Search academic libraries, organise papers, and build a literature survey in your Research Agent workspace.',
  },
  '/pdf-analysis': {
    title: 'PDF Analysis · Research Agent',
    description:
      'Upload a paper, inspect its structure, and ask questions grounded in the document from your Research Agent workspace.',
  },
  '/manuscript-builder': {
    title: 'Manuscript Builder · Research Agent',
    description:
      'Draft a research manuscript section by section with citations, revision tools, and saved versions in Research Agent.',
  },
  '/venue-recommendations': {
    title: 'Venue Recommendations · Research Agent',
    description:
      'Match your manuscript to journals and conferences and review submission guidance in Research Agent.',
  },
  '/admin': {
    title: 'Admin · Research Agent',
    description:
      'Monitor usage, APIs, and user accounts for Research Agent.',
  },
};

export const SIGNUP_IN_META = {
  title: "You're in · Research Agent",
  description:
    'Your Research Agent workspace is ready. Discover topics, survey literature, draft manuscripts, and match publication venues.',
};

export const NOT_FOUND_META = {
  title: 'Page not found · Research Agent',
  description:
    'This address is not a page on Research Agent. Check the URL or return to the home page or your workspace.',
};
