import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from '../lib/server/pythonRunner';
import {
	SAVED_SEARCH_FILTER_KEYS,
	normalizeSearch,
	searchBody
} from '../lib/server/collections';
import { DEFAULT_MAX_JOBS } from '../lib/taskLaunch';

const FIXTURE_PATH = path.join(getProjectRoot(), 'config', 'saved_search.cases.json');

interface SavedSearchCase {
	case: string;
	record: Record<string, any>;
	expected: {
		id: string;
		name: string;
		keyword: string;
		enable_search: boolean;
		enable_filter: boolean;
		target_action: string;
		target_task_type: string;
		max_jobs: number;
		channel_preference: string;
	};
}

interface SavedSearchFixture {
	filter_keys: string[];
	default_max_jobs: number;
	cases: SavedSearchCase[];
}

function loadFixture(): SavedSearchFixture {
	expect(fs.existsSync(FIXTURE_PATH), `${FIXTURE_PATH} is the cross-language pin`).toBe(true);
	return JSON.parse(fs.readFileSync(FIXTURE_PATH, 'utf-8'));
}

const fixture = loadFixture();

describe('SavedSearch cross-language mapping parity', () => {
	it('keeps declared filter keys and DEFAULT_MAX_JOBS aligned with the fixture', () => {
		expect([...SAVED_SEARCH_FILTER_KEYS]).toEqual(fixture.filter_keys);
		expect(DEFAULT_MAX_JOBS).toBe(fixture.default_max_jobs);
	});

	it.each(fixture.cases.map((c) => [c.case, c] as const))(
		'normalizes and serializes %s identically to Python',
		(_name, entry) => {
			const normalized = normalizeSearch(entry.record);
			expect(normalized.id).toBe(entry.expected.id);
			expect(normalized.name).toBe(entry.expected.name);
			expect(normalized.keyword).toBe(entry.expected.keyword);
			expect(normalized.enable_search).toBe(entry.expected.enable_search);
			expect(normalized.enable_filter).toBe(entry.expected.enable_filter);
			expect(normalized.target_action).toBe(entry.expected.target_action);
			expect(normalized.target_task_type).toBe(entry.expected.target_task_type);
			expect(normalized.max_jobs).toBe(entry.expected.max_jobs);
			expect(normalized.filter?.channel_preference).toBe(entry.expected.channel_preference);
			expect(Object.keys(normalized.filter || {}).sort()).toEqual(
				[...fixture.filter_keys].sort()
			);

			const wire = searchBody(normalized, normalized.id);
			const wireFilter = wire.filter as Record<string, unknown>;
			expect(Object.keys(wireFilter).sort()).toEqual([...fixture.filter_keys].sort());
			expect(wireFilter.unknown_legacy_extra).toBeUndefined();
			expect(wireFilter.channel_preference).toBe(entry.expected.channel_preference);
			expect(wire.target_action).toBe(entry.expected.target_action);
			expect(wire.target_task_type).toBe(entry.expected.target_task_type);
		}
	);
});
