import { marked } from 'marked';
import DOMPurify from 'dompurify';

export function renderMarkdown(md) {
  return DOMPurify.sanitize(marked.parse(md));
}
