import { describe, it, expect } from 'vitest';
import { _extractLastJson } from '../routes/api/match/critique/+server';

describe('extractLastJson', () => {
	it('returns the only JSON object when stdout is just JSON', () => {
		const out = '{"success":true,"revised_greeting":"hi"}';
		expect(_extractLastJson(out)).toBe(out);
	});

	it('ignores leading rich-formatted log lines and returns the trailing JSON', () => {
		// Simulates the bug: rich console.print outputs multi-line ANSI codes
		// and bracketed markers before the script's final JSON.dumps line.
		const leadingLog =
			'❌ LLM greeting refinement error: connection reset\n' +
			'\x1b[1;31m[bold red]\x1b[0m partial frame\n';
		const trailingJson = '{"success":false,"error":"LLM 微调失败"}';
		const stdout = leadingLog + trailingJson + '\n';

		const result = _extractLastJson(stdout);
		expect(result).not.toBeNull();
		expect(JSON.parse(result!)).toEqual({
			success: false,
			error: 'LLM 微调失败'
		});
	});

	it('handles nested objects and arrays correctly', () => {
		const out =
			'log noise\n{"success":true,"rule":{"id":"r1","tags":["a","b"],"nested":{"k":1}}}';
		const result = _extractLastJson(out);
		expect(result).not.toBeNull();
		expect(JSON.parse(result!)).toEqual({
			success: true,
			rule: { id: 'r1', tags: ['a', 'b'], nested: { k: 1 } }
		});
	});

	it('returns null when there is no balanced JSON object', () => {
		expect(_extractLastJson('no braces here')).toBeNull();
		expect(_extractLastJson('{unbalanced')).toBeNull();
		expect(_extractLastJson('')).toBeNull();
	});

	it('does not falsely match the first opening brace when later braces exist', () => {
		// Greedy-match would have grabbed the leading `{` from the log and
		// the trailing `}` from JSON, producing invalid JSON with embedded
		// control characters. extractLastJson must start at the LAST opening
		// brace that balances with the FINAL closing brace.
		const stdout = 'noise { bad } more noise\n{"a":1}';
		const result = _extractLastJson(stdout);
		expect(result).toBe('{"a":1}');
	});

	it('preserves literal Chinese characters and escape sequences inside JSON', () => {
		const out = '{"revised_greeting":"您好，\\n这是打招呼文案。"}';
		const result = _extractLastJson(out);
		expect(result).toBe(out);
		const parsed = JSON.parse(result!);
		expect(parsed.revised_greeting).toBe('您好，\n这是打招呼文案。');
	});
});
