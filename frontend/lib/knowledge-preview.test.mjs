import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';
const source = await readFile(new URL('./knowledge-preview.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { expectedPhraseFound } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('expected phrase is found in visible source quotes', () => {
  assert.equal(expectedPhraseFound('I found this in a source.', [{ text: 'Aug. 3, 2026 (Monday) Commencement of classes' }], 'Aug. 3, 2026'), true);
});

test('expected phrase matches markdown-escaped answer text', () => {
  assert.equal(expectedPhraseFound('Starts Aug\\. 3, 2026', [], 'Aug. 3, 2026'), true);
});

test('expected phrase matches HTML-escaped visible source text', () => {
  assert.equal(expectedPhraseFound('', [{ text: 'A &amp; B' }], 'A & B'), true);
});

test('expected phrase check is case-insensitive and trims input', () => {
  assert.equal(expectedPhraseFound('AUG. 3, 2026', [], '  aug. 3, 2026 '), true);
});

test('missing expected phrase remains a miss', () => {
  assert.equal(expectedPhraseFound('Classes resume in October.', [], 'Aug. 3, 2026'), false);
});
