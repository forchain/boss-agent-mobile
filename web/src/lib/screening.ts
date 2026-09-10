/**
 * Screening and Guardrail Utilities
 * Boss Agent Mobile
 */

export function isMaskedCompanyName(name: string | null | undefined): boolean {
	if (!name || typeof name !== 'string') {
		return false;
	}
	const cleaned = name.trim();
	if (!cleaned) {
		return false;
	}

	// Confidential markers
	if (cleaned.includes('***') || cleaned.includes('保密') || cleaned.includes('隐藏') || cleaned.includes('匿名')) {
		return true;
	}

	// Chinese placeholder character 某
	if (cleaned.includes('某')) {
		return true;
	}

	// Generic descriptors
	const genericRegex = /^(?:知名|头部|大型|中型|小型|外资|民营|上市|创业|初创)(?:互联网|科技|金融|量化|医疗|AI|人工智能)?(?:公司|企业|集团|机构|团队|厂商|大厂|外企)$/;
	if (genericRegex.test(cleaned)) {
		return true;
	}

	return false;
}

export function validateCanBlacklistCompany(
	companyName: string | null | undefined,
	isHeadhunter: boolean = false
): { allowed: boolean; notice: string } {
	if (!companyName || !companyName.trim()) {
		return { allowed: false, notice: '公司名称不能为空' };
	}

	const cleaned = companyName.trim();

	if (isHeadhunter) {
		return {
			allowed: false,
			notice: `【黑名单保护生效】岗位为猎头代招岗位，公司名称 "${cleaned}" 为聚合或代招渠道，禁止加入全局黑名单以避免误伤其他雇主`
		};
	}

	if (isMaskedCompanyName(cleaned)) {
		return {
			allowed: false,
			notice: `【黑名单保护生效】"${cleaned}" 属于保密/占位公司名称（如某...公司），禁止加入全局黑名单以避免大范围误伤不相关企业`
		};
	}

	return {
		allowed: true,
		notice: `公司 "${cleaned}" 为真实直招企业，允许加入黑名单`
	};
}

export function cleanJobTitle(rawTitle: string | null | undefined): string {
	if (!rawTitle || typeof rawTitle !== 'string') {
		return '';
	}
	let t = rawTitle.trim();
	while (true) {
		const cleaned = t.replace(/[\s&@%]+$/g, '').trim();
		if (cleaned === t) {
			break;
		}
		t = cleaned;
	}
	return t;
}

export function extractDigestFromJd(jd: string | null | undefined, maxChars = 100): string {
	if (!jd || typeof jd !== 'string') {
		return '';
	}
	const lines = jd.split('\n').map((l) => l.trim()).filter(Boolean);
	const substantive: string[] = [];
	for (const line of lines) {
		const stripped = line.replace(/^[0-9一二三四五六七八九十、.·•*-\s]+/, '').trim();
		// Skip section headers
		if (
			!stripped ||
			stripped.length < 5 ||
			/^(?:岗位职责|工作职责|职位描述|任职要求|任职资格|加分项|基本要求|必须要求|关于我们|公司介绍|加分条件|薪酬福利)[:：]?$/.test(stripped) ||
			/^【(?:岗位职责|工作职责|职位描述|任职要求|任职资格|加分项|关于我们)】$/.test(stripped)
		) {
			continue;
		}
		substantive.push(stripped);
		if (substantive.join('；').length >= 35) {
			break;
		}
	}
	let res = substantive.join('；') || (lines.length > 0 ? lines[0] : '');
	if (res.length > maxChars) {
		res = res.slice(0, maxChars).replace(/[，；、\s]+$/, '') + '...';
	}
	return res;
}

const COMMON_TECH_TAGS = [
	'Java', 'Python', 'Go', 'Golang', 'Rust', 'C++', 'C#', '.NET', 'PHP',
	'React Native', 'React', 'Flutter', 'Vue', 'Angular', 'Node.js', 'TypeScript', 'JavaScript',
	'Android', 'iOS', '鸿蒙', 'HarmonyOS', '小程序', 'RN',
	'LLM', 'AI', '大模型', 'Agent', 'Prompt', 'RAG', 'AIGC', 'NLP', 'CV', '机器学习', '深度学习',
	'Spring', 'SpringBoot', 'FastAPI', 'Django', 'Flask',
	'MySQL', 'PostgreSQL', 'Redis', 'MongoDB', 'Elasticsearch', 'Kafka',
	'Kubernetes', 'K8s', 'Docker', 'DevOps', 'CI/CD',
	'全栈', '架构师', '前端', '后端', '移动端', '测开', '运维', '微服务',
	'3-5年', '5-10年', '1-3年', '10年以上', '应届生',
	'本科', '硕士', '博士', '大专'
];

export function extractTagsFromText(text: string | null | undefined): string[] {
	if (!text || typeof text !== 'string') {
		return [];
	}
	const matched: string[] = [];
	for (const tag of COMMON_TECH_TAGS) {
		const escaped = tag.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&');
		const regex = new RegExp(`(?:^|[^a-zA-Z0-9_])${escaped}(?:$|[^a-zA-Z0-9_])`, 'i');
		if (regex.test(text)) {
			matched.push(tag);
			if (matched.length >= 5) break;
		}
	}
	return matched;
}
