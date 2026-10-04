const lexicalWords = new RegExp("[\\p{L}\\p{N}]+(?:['’][\\p{L}\\p{N}]+)*", "gu");

const standaloneStopWords = new Set(
  "a an and are as at be been being but by can could did do does for from had has have he her here him his how i if in into is it its me my no not of on or our she should so than that the their them there these they this those to us was we were what when where which who why will with would you your".split(" "),
);

function normalizedWord(word: string): string {
  return word.normalize("NFKC").replaceAll("’", "'").toLowerCase();
}

function words(text: string) {
  return Array.from(text.matchAll(lexicalWords), (match) => ({
    value: normalizedWord(match[0]),
    start: match.index,
    end: match.index + match[0].length,
  }));
}

const meaningQuestions = [
  /^what\s+(?:does|do)\s+(.+?)\s+mean(?:\s+(?:here|in\s+(?:this|the)\s+(?:sentence|passage|context|paragraph|story)))?$/i,
  /^what(?:'s|\s+is)\s+(?:the\s+)?(?:meaning|definition)\s+of\s+(.+)$/i,
  /^(?:define|pronounce|meaning\s+of|definition\s+of)\s+(.+)$/i,
  /^explain\s+(?:the\s+)?(?:word|phrase|term|expression)\s+(.+)$/i,
  /^how\s+(?:do\s+(?:you|i)|should\s+i)\s+pronounce\s+(.+)$/i,
  /^what(?:'s|\s+is)\s+(["“‘'].+["”’'])$/i,
];

/** Return the exact page wording of a term explicitly asked about, when present. */
export function findAskedTerm(question: string, pageText: string): string | null {
  const prompt = question.trim()
    .replace(/[?!.…]+$/u, "")
    .replace(/^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?(?:tell\s+me\s+)?/i, "")
    .trim();
  let candidate = meaningQuestions.map((pattern) => prompt.match(pattern)?.[1]).find(Boolean);
  if (!candidate) return null;

  candidate = candidate.trim().replace(/^(?:the\s+)?(?:word|phrase|term|expression)\s+/i, "");
  const closingQuotes: Record<string, string> = { '"': '"', "'": "'", "“": "”", "‘": "’" };
  const closingQuote = closingQuotes[candidate[0]];
  if (closingQuote) {
    if (!candidate.endsWith(closingQuote)) return null;
    candidate = candidate.slice(1, -1).trim();
  }
  if (!candidate || candidate.length > 100) return null;

  const requestedWords = words(candidate);
  if (!requestedWords.length || requestedWords.every((word) => standaloneStopWords.has(word.value))) return null;

  const pageWords = words(pageText);
  for (let index = 0; index <= pageWords.length - requestedWords.length; index += 1) {
    if (requestedWords.every((word, offset) => word.value === pageWords[index + offset].value)) {
      return pageText.slice(pageWords[index].start, pageWords[index + requestedWords.length - 1].end);
    }
  }
  return null;
}

/** Count distinct lexical words across requested terms, regardless of case. */
export function countAskedWords(terms: string[]): number {
  return new Set(terms.flatMap((term) => words(term).map((word) => word.value))).size;
}
