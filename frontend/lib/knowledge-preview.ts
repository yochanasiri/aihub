type Quote = { text: string };

function normalizeVisibleText(value: string): string {
  return value
    .replace(/\\([\\`*_{}\[\]()#+.!|>~-])/g, '$1')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/gi, "'")
    .toLocaleLowerCase();
}

export function expectedPhraseFound(answer: string, quotes: Quote[], expected: string): boolean {
  const visibleText = normalizeVisibleText([answer, ...quotes.map((quote) => quote.text)].join('\n'));
  return visibleText.includes(normalizeVisibleText(expected.trim()));
}
