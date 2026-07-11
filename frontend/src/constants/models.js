export const MODELS = [
  { id: 'groq-default', provider: 'groq', model: null, label: 'Groq (Llama 3.3 70B)', group: 'Other Providers' },
  { id: 'openrouter-default', provider: 'openrouter', model: null, label: 'OpenRouter (Claude)', group: 'Other Providers' },
  { id: 'nvidia-default', provider: 'nvidia', model: null, label: 'NVIDIA (Smoke Test)', group: 'Other Providers' },
  { id: 'deepseek-v3.2', provider: 'openrouter', model: 'deepseek/deepseek-v3.2', label: 'DeepSeek V3.2 (cheap, high quality)', group: 'OpenRouter Models' },
  { id: 'kimi-k2.6', provider: 'openrouter', model: 'moonshotai/kimi-k2.6', label: 'Kimi K2.6 (256K context, auto-cache)', group: 'OpenRouter Models' },

  { id: 'openai-default', provider: 'openai', model: null, label: 'OpenAI GPT-4o', group: 'Other Providers' },
  { id: 'gemini-2.0-flash', provider: 'gemini', model: 'gemini-2.0-flash', label: 'Gemini 2.0 Flash', group: 'Gemini' },
  // Note: Google deprecates Gemini models frequently (multiple cutovers in 2026 alone).
  // Recommend checking ai.google.dev/gemini-api/docs/models periodically, or prefer
  // -latest aliases over pinned versions going forward for any new Gemini integration.
  { id: 'gemini-flash-latest', provider: 'gemini', model: 'gemini-flash-latest', label: 'Gemini Flash (Latest)', group: 'Gemini' },
  { id: 'gemma-4-27b-it', provider: 'gemini', model: 'gemma-4-27b-it', label: 'Gemma 4 27B (free, open-weight)', group: 'Gemma (open-weight)' },
  { id: 'gemma-4-26b-a4b-it', provider: 'gemini', model: 'gemma-4-26b-a4b-it', label: 'Gemma 4 26B MoE (fast, free)', group: 'Gemma (open-weight)' }
];
