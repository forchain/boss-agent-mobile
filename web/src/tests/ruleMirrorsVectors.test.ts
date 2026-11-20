import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from '../lib/server/pythonRunner';
import { greetingProvenance } from '../lib/greetingProvenance';
import { normalizeCommuteLimit } from '../lib/commute';
import { normalizeChatAcknowledgment } from '../lib/chatAcknowledgment';
import { isCommunicationExpired } from '../lib/server/communication';
import {
	COMMON_COMPOUND_SURNAMES,
	formatRecruiterGreetingPrefix,
	parseRecruiterTitle
} from '../lib/server/salutation';

const FIXTURE_PATH = path.join(getProjectRoot(), 'config', 'rule_mirrors.vectors.json');

interface RuleMirrorsFixture {
	greeting_provenance: Array<{
		case: string;
		record: { greeting_message?: string | null; greeting_source?: string | null };
		expected_is_human: boolean;
	}>;
	commute_limit: Array<{
		case: string;
		input: unknown;
		expected: number | null;
	}>;
	chat_acknowledgment_positive_int: Array<{
		case: string;
		input: unknown;
		fallback: number;
		expected: number;
	}>;
	cooldown_timestamps: Array<{
		case: string;
		record: { applied_at?: unknown; created?: unknown };
		cooldown_days: number;
		now: string;
		expected_expired: boolean;
	}>;
	compound_surnames: string[];
	salutation: Array<{
		case: string;
		raw: string;
		expected_title: string;
		expected_prefix: string;
	}>;
}

function loadFixture(): RuleMirrorsFixture {
	expect(fs.existsSync(FIXTURE_PATH), `${FIXTURE_PATH} is the cross-language pin`).toBe(true);
	return JSON.parse(fs.readFileSync(FIXTURE_PATH, 'utf-8'));
}

const fixture = loadFixture();

describe('Rule Mirrors cross-language parity', () => {
	it.each(fixture.greeting_provenance.map((v) => [v.case, v] as const))(
		'evaluates greeting provenance %s identically to Python',
		(_name, vector) => {
			expect(greetingProvenance(vector.record) === 'human').toBe(vector.expected_is_human);
		}
	);

	it.each(fixture.commute_limit.map((v) => [v.case, v] as const))(
		'coerces commute limit %s identically to Python',
		(_name, vector) => {
			expect(normalizeCommuteLimit(vector.input as any)).toBe(vector.expected);
		}
	);

	it.each(fixture.chat_acknowledgment_positive_int.map((v) => [v.case, v] as const))(
		'coerces chat positive int %s identically to Python',
		(_name, vector) => {
			// Exercise with max_scan_depth when fallback=30, max_scroll_swipes when fallback=5
			const normalized = normalizeChatAcknowledgment(
				vector.fallback === 30
					? { max_scan_depth: vector.input }
					: { max_scroll_swipes: vector.input }
			);
			const actual =
				vector.fallback === 30 ? normalized.max_scan_depth : normalized.max_scroll_swipes;
			expect(actual).toBe(vector.expected);
		}
	);

	it.each(fixture.cooldown_timestamps.map((v) => [v.case, v] as const))(
		'evaluates cooldown timestamp %s identically to Python',
		(_name, vector) => {
			const nowMs = Date.parse(vector.now);
			expect(isCommunicationExpired(vector.record, vector.cooldown_days, nowMs)).toBe(
				vector.expected_expired
			);
		}
	);

	it('keeps COMMON_COMPOUND_SURNAMES aligned with the shared fixture', () => {
		expect(COMMON_COMPOUND_SURNAMES).toEqual(new Set(fixture.compound_surnames));
	});

	it.each(fixture.salutation.map((v) => [v.case, v] as const))(
		'parses salutation %s identically to Python',
		(_name, vector) => {
			expect(parseRecruiterTitle(vector.raw)).toBe(vector.expected_title);
			expect(formatRecruiterGreetingPrefix(vector.raw)).toBe(vector.expected_prefix);
		}
	);
});
