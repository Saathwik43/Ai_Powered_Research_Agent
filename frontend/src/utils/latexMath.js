/**
 * remark-math / KaTeX only parse `$...$` and `$$...$$`.
 * Models still emit LaTeX `\[...\]` / `\(...\)`, and Markdown then treats
 * `\[` as an escaped `[`, so the formula prints as `[ L_n = \frac{...} ]`.
 */

const FENCE_RE = /```[\s\S]*?```/g;

export function normalizeLatexDelimiters(md) {
  if (!md) return md;
  const fences = [];
  let out = String(md).replace(FENCE_RE, (block) => {
    fences.push(block);
    return `\u0000FENCE${fences.length - 1}\u0000`;
  });

  out = out.replace(/\\\[([\s\S]*?)\\\]/g, (_, inner) => `$$${inner}$$`);
  out = out.replace(/\\\(([\s\S]*?)\\\)/g, (_, inner) => `$${inner}$`);

  return out.replace(/\u0000FENCE(\d+)\u0000/g, (_, i) => fences[Number(i)]);
}

export const KATEX_REHYPE_OPTIONS = {
  strict: false,
  throwOnError: false,
  // KaTeX paints unparseable source in this colour, with the parse error as
  // its title. 'inherit' made broken maths look like ordinary text (ME-3, B7).
  errorColor: 'var(--danger)',
};
