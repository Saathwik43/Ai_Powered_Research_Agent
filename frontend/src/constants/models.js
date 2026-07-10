export const MODELS = [
  { id: 'groq-default', provider: 'groq', model: null, label: 'Groq (Llama 3.3 70B)', group: 'Other Providers' },
  { id: 'openrouter-default', provider: 'openrouter', model: null, label: 'OpenRouter (Claude)', group: 'Other Providers' },
  { id: 'openai-default', provider: 'openai', model: null, label: 'OpenAI GPT-4o', group: 'Other Providers' },
  { id: 'gemini-2.0-flash', provider: 'gemini', model: 'gemini-2.0-flash', label: 'Gemini 2.0 Flash', group: 'Gemini' },
  { id: 'gemini-2.5-flash', provider: 'gemini', model: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash', group: 'Gemini' },
  { id: 'gemma-4-27b-it', provider: 'gemini', model: 'gemma-4-27b-it', label: 'Gemma 4 27B (free, open-weight)', group: 'Gemma (open-weight)' },
  { id: 'gemma-4-26b-a4b-it', provider: 'gemini', model: 'gemma-4-26b-a4b-it', label: 'Gemma 4 26B MoE (fast, free)', group: 'Gemma (open-weight)' }
];
