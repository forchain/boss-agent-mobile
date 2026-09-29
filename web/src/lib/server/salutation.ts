import { normalizeRecruiterName } from './jobFingerprint';

/**
 * Chinese compound surnames recognized for salutation formatting.
 * Mirrors COMMON_COMPOUND_SURNAMES in src/boss_agent/models.py.
 */
export const COMMON_COMPOUND_SURNAMES: ReadonlySet<string> = new Set([
	'欧阳',
	'太史',
	'端木',
	'上官',
	'司马',
	'东方',
	'独孤',
	'南宫',
	'万俟',
	'闻人',
	'夏侯',
	'诸葛',
	'尉迟',
	'公羊',
	'赫连',
	'澹台',
	'皇甫',
	'宗政',
	'濮阳',
	'公冶',
	'太叔',
	'申屠',
	'公孙',
	'慕容',
	'仲孙',
	'钟离',
	'长孙',
	'宇文',
	'司徒',
	'司空',
	'司寇',
	'子车',
	'微生',
	'呼延',
	'拓跋',
	'乐正',
	'壤驷',
	'公良',
	'漆雕',
	'巫马',
	'公西',
	'令狐'
]);

/**
 * Generic recruiter placeholders that should fall back to standard greeting.
 */
export const GENERIC_ROLE_TOKENS: ReadonlySet<string> = new Set([
	'hr',
	'hrbp',
	'猎头',
	'猎头顾问',
	'招聘者',
	'招聘',
	'招聘顾问',
	'招聘专员',
	'招聘经理',
	'人事',
	'人事经理',
	'顾问',
	'面试官',
	'管理员',
	'工作人员',
	'合伙人',
	'团队',
	'部门'
]);

/**
 * Parse a recruiter's name string into an appropriate Chinese salutation title.
 *
 * Supported patterns:
 * - 'xx 女士' -> 'xx女士'
 * - 'xx 先生' -> 'xx先生'
 * - 'xxx(真名)' -> 'x总' or 'xx总' (compound surnames)
 * - 'xx 总' -> 'xx总'
 * - Pure English, numbers, generic roles, or missing/abnormal names -> ''
 */
export function parseRecruiterTitle(raw: string | null | undefined): string {
	if (!raw) return '';
	let text = String(raw).trim();
	if (!text) return '';

	// 1. Normalize spaces before gendered suffixes e.g. "张 女士" -> "张女士"
	text = text.replace(/\s*(女士|先生)/g, '$1');

	// 2. Strip Boss separator and attached title (e.g. "钟先生 · 猎头顾问" -> "钟先生")
	let name = normalizeRecruiterName(text);

	// 3. Strip brackets/parentheses (e.g. "张女士(HR)", "李女士【招聘】")
	name = name.replace(/[\(（\[【].*?[\)）\]】]/g, '').trim();

	// 4. Strip trailing role tokens separated by whitespace or delimiter
	name = name.split(/[\s/|_\-]+/)[0].trim();

	if (!name) return '';

	if (GENERIC_ROLE_TOKENS.has(name.toLowerCase())) {
		return '';
	}

	// Must contain only Chinese characters
	if (!/^[\u4e00-\u9fa5]+$/.test(name)) {
		return '';
	}

	// Check "xx女士", "xx先生", "xx总"
	if (name.endsWith('女士')) {
		if (name.length >= 3) return name;
		return '';
	}
	if (name.endsWith('先生')) {
		if (name.length >= 3) return name;
		return '';
	}
	if (name.endsWith('总')) {
		if (name.length === 2 || name.length === 3) return name;
		return '';
	}

	// Real name (2-4 characters)
	if (name.length >= 2 && name.length <= 4) {
		if (name.length >= 3 && COMMON_COMPOUND_SURNAMES.has(name.slice(0, 2))) {
			return `${name.slice(0, 2)}总`;
		}
		return `${name[0]}总`;
	}

	return '';
}

/**
 * Format the opening greeting prefix for a recruiter.
 *
 * - 'xx 女士' -> 'xx女士您好,幸会!'
 * - 'xx 先生' -> 'xx先生您好,幸会!'
 * - 'xxx(真名)' -> 'x总您好,幸会!' or 'xx总您好,幸会!' (compound surname)
 * - Fallback -> '您好,幸会!'
 */
export function formatRecruiterGreetingPrefix(raw: string | null | undefined): string {
	const title = parseRecruiterTitle(raw);
	if (title) {
		return `${title}您好,幸会!`;
	}
	return '您好,幸会!';
}
