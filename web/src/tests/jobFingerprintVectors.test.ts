import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from '../lib/server/pythonRunner';
import { computeFingerprint, normalizeRecruiterName } from '../lib/server/jobFingerprint';

// Cross-language Job Fingerprint parity — the TypeScript half. The same fixture is
// asserted by `tests/unit/test_job_fingerprint_vectors.py`, so the two
// implementations cannot drift apart without one of the suites going red.

const FIXTURE_PATH = path.join(getProjectRoot(), 'config', 'fingerprint.vectors.json');

interface FingerprintVector {
	case: string;
	company_name: string;
	title: string;
	recruiter_name: string;
	fingerprint: string;
}

function vectors(): FingerprintVector[] {
	expect(fs.existsSync(FIXTURE_PATH), `${FIXTURE_PATH} is the cross-language pin`).toBe(true);
	return JSON.parse(fs.readFileSync(FIXTURE_PATH, 'utf-8')).vectors;
}

describe('Job Fingerprint cross-language parity', () => {
	it.each(vectors().map((v) => [v.case, v] as const))(
		'computes the shared vector %s identically',
		(_case, vector) => {
			expect(
				computeFingerprint(vector.company_name, vector.title, vector.recruiter_name)
			).toBe(vector.fingerprint);
		}
	);

	it('strips the recruiter title Boss appends after a separator', () => {
		expect(normalizeRecruiterName('张三·猎头顾问')).toBe('张三');
		expect(normalizeRecruiterName('李四•HRBP')).toBe('李四');
		expect(normalizeRecruiterName('王五・招聘经理')).toBe('王五');
		expect(normalizeRecruiterName('赵六·')).toBe('赵六');
		expect(normalizeRecruiterName('钱七·顾问·北京')).toBe('钱七');
		expect(normalizeRecruiterName('')).toBe('');
		expect(normalizeRecruiterName(null)).toBe('');
	});

	it('keeps the two recruiter spellings of one person on the same key', () => {
		// The regression that put duplicate Job Records in the Unmatched Job Stream.
		const plain = vectors().find((v) => v.case === 'plain-name')!;
		const suffixed = vectors().find((v) => v.case === 'recruiter-middot-title')!;
		expect(plain.recruiter_name).not.toBe(suffixed.recruiter_name);
		expect(computeFingerprint(plain.company_name, plain.title, plain.recruiter_name)).toBe(
			computeFingerprint(suffixed.company_name, suffixed.title, suffixed.recruiter_name)
		);
	});
});
