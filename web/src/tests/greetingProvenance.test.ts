/**
 * Greeting provenance (issue #300) — the pure half.
 *
 * The rule the whole feature rests on is one function deep, and the part that matters is
 * what an *unknown* source means: a record written before provenance existed is not a
 * human's approval, and treating it as one would freeze an old machine draft in place and
 * send it as though somebody had signed it off.
 */
import { describe, it, expect } from 'vitest';
import {
	GREETING_SOURCE_AGENT,
	GREETING_SOURCE_HUMAN,
	greetingProvenance,
	greetingProvenanceLabel,
	humanGreetingPatch
} from '$lib/greetingProvenance';

describe('greeting provenance classification (issue #300)', () => {
	it('calls a human-marked copy the human’s', () => {
		expect(
			greetingProvenance({ greeting_message: '李工您好', greeting_source: GREETING_SOURCE_HUMAN })
		).toBe('human');
	});

	it('calls an agent-marked draft the agent’s', () => {
		expect(
			greetingProvenance({ greeting_message: '生成的招呼语', greeting_source: GREETING_SOURCE_AGENT })
		).toBe('agent');
	});

	it('treats a record with no known source as not human', () => {
		// Legacy rows, a column that arrived empty, and a value from some other writer all
		// land here. The safe direction is the agent's: worst case it re-drafts.
		for (const source of [undefined, null, '', '  ', 'who_knows']) {
			expect(
				greetingProvenance({ greeting_message: '历史草稿', greeting_source: source }),
				String(source)
			).toBe('unknown');
		}
	});

	it('has no provenance at all when there is no text', () => {
		// A `human` marker with an empty body must not read as an approved copy, or the run
		// would "send verbatim" nothing.
		expect(greetingProvenance({ greeting_message: '', greeting_source: GREETING_SOURCE_HUMAN }))
			.toBe('none');
		expect(greetingProvenance({ greeting_message: '   ', greeting_source: GREETING_SOURCE_HUMAN }))
			.toBe('none');
		expect(greetingProvenance(null)).toBe('none');
		expect(greetingProvenance(undefined)).toBe('none');
	});

	it('says the difference out loud in the panel’s words', () => {
		expect(greetingProvenanceLabel('human')).toContain('人工稿');
		expect(greetingProvenanceLabel('agent')).toContain('Agent 草稿');
		expect(greetingProvenanceLabel('unknown')).toContain('来源未知');
		expect(greetingProvenanceLabel('none')).toBe('');
	});

	it('moves the text and its provenance together in one patch', () => {
		expect(humanGreetingPatch('  李工您好  ')).toEqual({
			greeting_message: '  李工您好  ',
			greeting_source: GREETING_SOURCE_HUMAN
		});
	});
});
