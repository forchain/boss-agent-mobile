import { describe, it, expect } from 'vitest';
import { parseRecruiterTitle, formatRecruiterGreetingPrefix } from '../lib/server/salutation';

describe('Salutation and greeting prefix parsing (TS parity)', () => {
	const cases: Array<[string | null | undefined, string, string]> = [
		// xx 女士
		['张女士', '张女士', '张女士您好,幸会!'],
		['张女士 · HR', '张女士', '张女士您好,幸会!'],
		['李女士•资深顾问', '李女士', '李女士您好,幸会!'],
		['王女士 (HRBP)', '王女士', '王女士您好,幸会!'],
		['欧阳女士', '欧阳女士', '欧阳女士您好,幸会!'],
		// xx 先生
		['钟先生', '钟先生', '钟先生您好,幸会!'],
		['钟先生 · 猎头顾问', '钟先生', '钟先生您好,幸会!'],
		['司马先生', '司马先生', '司马先生您好,幸会!'],
		// xxx (真名) - Compound surnames
		['诸葛孔明', '诸葛总', '诸葛总您好,幸会!'],
		['诸葛亮', '诸葛总', '诸葛总您好,幸会!'],
		['欧阳六', '欧阳总', '欧阳总您好,幸会!'],
		['司马迁', '司马总', '司马总您好,幸会!'],
		['上官婉儿', '上官总', '上官总您好,幸会!'],
		// xxx (真名) - Single surnames
		['张伟', '张总', '张总您好,幸会!'],
		['王小明', '王总', '王总您好,幸会!'],
		['李明', '李总', '李总您好,幸会!'],
		// Existing xx总
		['张总', '张总', '张总您好,幸会!'],
		['诸葛总', '诸葛总', '诸葛总您好,幸会!'],
		// Fallbacks (English, numbers, generic role placeholders, empty)
		['Alice', '', '您好,幸会!'],
		['Bob Smith', '', '您好,幸会!'],
		['Tom · HR', '', '您好,幸会!'],
		['HR', '', '您好,幸会!'],
		['招聘专员', '', '您好,幸会!'],
		['猎头顾问', '', '您好,幸会!'],
		['12345', '', '您好,幸会!'],
		['', '', '您好,幸会!'],
		[null, '', '您好,幸会!'],
		[undefined, '', '您好,幸会!'],
	];

	for (const [raw, expectedTitle, expectedPrefix] of cases) {
		it(`correctly parses ${JSON.stringify(raw)}`, () => {
			expect(parseRecruiterTitle(raw)).toBe(expectedTitle);
			expect(formatRecruiterGreetingPrefix(raw)).toBe(expectedPrefix);
		});
	}
});
