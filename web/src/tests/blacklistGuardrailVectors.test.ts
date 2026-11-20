import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from '../lib/server/pythonRunner';
import {
	HEADHUNTER_AGENCY_KEYWORDS,
	isHeadhunterAgencyName,
	isMaskedCompanyName,
	validateCanBlacklistCompany
} from '../lib/screening';

const FIXTURE_PATH = path.join(getProjectRoot(), 'config', 'blacklist_guardrail.vectors.json');

interface GuardrailVector {
	case: string;
	company_name: string;
	is_headhunter: boolean;
	expected_allowed: boolean;
	expected_is_agency: boolean;
	expected_is_masked: boolean;
}

interface GuardrailFixture {
	agency_keywords: string[];
	vectors: GuardrailVector[];
}

function loadFixture(): GuardrailFixture {
	expect(fs.existsSync(FIXTURE_PATH), `${FIXTURE_PATH} is the cross-language pin`).toBe(true);
	return JSON.parse(fs.readFileSync(FIXTURE_PATH, 'utf-8'));
}

const fixture = loadFixture();

describe('Blacklist Guardrail cross-language parity', () => {
	it('keeps HEADHUNTER_AGENCY_KEYWORDS aligned with the shared fixture', () => {
		expect([...HEADHUNTER_AGENCY_KEYWORDS]).toEqual(fixture.agency_keywords);
	});

	it.each(fixture.vectors.map((v) => [v.case, v] as const))(
		'evaluates %s identically to Python',
		(_case, vector) => {
			expect(isHeadhunterAgencyName(vector.company_name)).toBe(vector.expected_is_agency);
			expect(isMaskedCompanyName(vector.company_name)).toBe(vector.expected_is_masked);
			const result = validateCanBlacklistCompany(vector.company_name, vector.is_headhunter);
			expect(result.allowed).toBe(vector.expected_allowed);
		}
	);
});
