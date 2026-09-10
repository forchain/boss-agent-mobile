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
