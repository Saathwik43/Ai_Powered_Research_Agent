import React from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { ghcolors, oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { useTheme } from '../context/ThemeContext';

export default function CodeHighlight({ language, children, ...props }) {
  const { resolvedTheme } = useTheme();
  return (
    <SyntaxHighlighter
      style={resolvedTheme === 'dark' ? oneDark : ghcolors}
      language={language}
      PreTag="div"
      {...props}
    >
      {children}
    </SyntaxHighlighter>
  );
}
