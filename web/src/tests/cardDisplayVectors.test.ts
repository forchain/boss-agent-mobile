import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from '../lib/server/pythonRunner';
import { extractDigestFromJd, getJobTags } from '../lib/screening';
import type { JobRecord } from '../lib/types';

const FIXTURE_PATH = path.join(getProjectRoot(), 'config', 'card_display.vectors.json');

interface DigestVector {
	case: string;
	jd: string;
	max_chars: number;
	expected: string;
}

interface TagVector {
	case: string;
	job: {
		title: string;
		company_name: string;
		recruiter_name: string;
		recruiter_title: string;
		location: string;
	};
	raw_tags: string[];
	expected: string[];
}

function loadFixture(): { digest_vectors: DigestVector[]; tag_vectors: TagVector[] } {
	expect(fs.existsSync(FIXTURE_PATH), `${FIXTURE_PATH} is the cross-language pin`).toBe(true);
	return JSON.parse(fs.readFileSync(FIXTURE_PATH, 'utf-8'));
}

const fixture = loadFixture();

describe('Card Display cross-language parity', () => {
	it.each(fixture.digest_vectors.map((v) => [v.case, v] as const))(
		'extracts digest for %s identically to Python',
		(_name, vector) => {
			expect(extractDigestFromJd(vector.jd, vector.max_chars)).toBe(vector.expected);
		}
	);

	it.each(fixture.tag_vectors.map((v) => [v.case, v] as const))(
		'sanitizes tags for %s identically to Python',
		(_name, vector) => {
			const job: JobRecord = {
				id: 'job_1',
				fingerprint: 'fp_1',
				status: 'jd_saved',
				title: vector.job.title,
				company_name: vector.job.company_name,
				recruiter_name: vector.job.recruiter_name,
				recruiter_title: vector.job.recruiter_title,
				location: vector.job.location,
				tags: vector.raw_tags
			};
			expect(getJobTags(job)).toEqual(vector.expected);
		}
	);
});
