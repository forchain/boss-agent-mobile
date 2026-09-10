import { describe, it, expect } from 'vitest';
import { isMaskedCompanyName, validateCanBlacklistCompany } from '../lib/screening';
import { GET, POST, DELETE } from '../routes/api/screening/blacklist/+server';

describe('Masked Company Guardrail & Screening Utilities', () => {
	it('correctly identifies masked and placeholder company names', () => {
		expect(isMaskedCompanyName('某中型人工智能公司')).toBe(true);
		expect(isMaskedCompanyName('成都某中型...智能公司')).toBe(true);
		expect(isMaskedCompanyName('某知名互联网公司')).toBe(true);
		expect(isMaskedCompanyName('某大型国企')).toBe(true);
		expect(isMaskedCompanyName('***公司')).toBe(true);
		expect(isMaskedCompanyName('***')).toBe(true);
		expect(isMaskedCompanyName('保密公司')).toBe(true);
		expect(isMaskedCompanyName('知名外企')).toBe(true);
	});

	it('preserves authentic registered employer names', () => {
		expect(isMaskedCompanyName('深至科技')).toBe(false);
		expect(isMaskedCompanyName('游族网络')).toBe(false);
		expect(isMaskedCompanyName('腾讯科技（深圳）有限公司')).toBe(false);
		expect(isMaskedCompanyName('北京字节跳动网络技术有限公司')).toBe(false);
		expect(isMaskedCompanyName('Google LLC')).toBe(false);
	});

	it('validates blacklisting permissions with guardrail enforcement', () => {
		// Authentic direct company -> allowed
		const directAuth = validateCanBlacklistCompany('深至科技', false);
		expect(directAuth.allowed).toBe(true);
		expect(directAuth.notice).toContain('真实直招企业');

		// Masked direct company -> blocked
		const directMasked = validateCanBlacklistCompany('某中型人工智能公司', false);
		expect(directMasked.allowed).toBe(false);
		expect(directMasked.notice).toContain('保密/占位公司名称');

		// Headhunter agency posting -> blocked regardless of name
		const hhAuth = validateCanBlacklistCompany('深至科技', true);
		expect(hhAuth.allowed).toBe(false);
		expect(hhAuth.notice).toContain('猎头代招岗位');

		// Empty company name -> blocked
		const emptyComp = validateCanBlacklistCompany('', false);
		expect(emptyComp.allowed).toBe(false);
	});

	it('blocks masked and headhunter companies in blacklist API endpoint', async () => {
		// Masked company POST should fail
		const maskedReq = new Request('http://localhost/api/screening/blacklist', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ company_name: '某中型人工智能公司', is_headhunter: false })
		});
		const maskedResp = await POST({ request: maskedReq } as any);
		expect(maskedResp.status).toBe(400);
		const maskedData = await maskedResp.json();
		expect(maskedData.allowed).toBe(false);

		// Headhunter POST should fail
		const hhReq = new Request('http://localhost/api/screening/blacklist', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ company_name: '游族网络', is_headhunter: true })
		});
		const hhResp = await POST({ request: hhReq } as any);
		expect(hhResp.status).toBe(400);
		const hhData = await hhResp.json();
		expect(hhData.allowed).toBe(false);

		// Authentic direct company POST should succeed
		const directReq = new Request('http://localhost/api/screening/blacklist', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ company_name: '深至科技', is_headhunter: false })
		});
		const directResp = await POST({ request: directReq } as any);
		expect(directResp.status).toBe(200);
		const directData = await directResp.json();
		expect(directData.success).toBe(true);
		expect(directData.allowed).toBe(true);
		expect(directData.company_blacklist).toContain('深至科技');

		// GET retrieves blacklist
		const getResp = await GET({} as any);
		const getData = await getResp.json();
		expect(getData.company_blacklist).toContain('深至科技');

		// Clean up via DELETE
		const delReq = new Request('http://localhost/api/screening/blacklist', {
			method: 'DELETE',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ company_name: '深至科技' })
		});
		const delResp = await DELETE({ request: delReq } as any);
		const delData = await delResp.json();
		expect(delData.company_blacklist).not.toContain('深至科技');
	});

	it('cleanJobTitle strips trailing @%, &@, %, @ and whitespace', async () => {
		const { cleanJobTitle } = await import('../lib/screening');
		expect(cleanJobTitle('外企-全栈开发工程师-不加班1075 @%')).toBe('外企-全栈开发工程师-不加班1075');
		expect(cleanJobTitle('资深架构师 &@')).toBe('资深架构师');
		expect(cleanJobTitle('算法高级工程师-DataAgent &@  &@')).toBe('算法高级工程师-DataAgent');
		expect(cleanJobTitle('前端开发 %')).toBe('前端开发');
		expect(cleanJobTitle('移动端架构师 @')).toBe('移动端架构师');
		expect(cleanJobTitle('')).toBe('');
		expect(cleanJobTitle(null as any)).toBe('');
	});

	it('extractDigestFromJd extracts concise summary without raw headers', async () => {
		const { extractDigestFromJd } = await import('../lib/screening');
		const rawJd = `岗位职责
负责公司 后端与前端系统的设计、开发与迭代
使用 Java 构建和维护核心后端服务，保障系统稳定性与扩展性
负责混合 App（RN）相关功能开发

任职要求（必须）
扎实的 Java 后端开发经验，具备完整项目经验`;

		const digest = extractDigestFromJd(rawJd, 100);
		expect(digest).toContain('负责公司 后端与前端系统的设计、开发与迭代');
		expect(digest.length).toBeLessThanOrEqual(100);
		expect(digest).not.toContain('任职要求');
	});

	it('extractTagsFromText matches relevant skill and requirement tags', async () => {
		const { extractTagsFromText } = await import('../lib/screening');
		const text = '外企-全栈开发工程师 Java 后端开发 React Native Flutter 混合开发 本科 3-5年';
		const tags = extractTagsFromText(text);
		expect(tags).toContain('Java');
		expect(tags.some((t) => t === 'React Native' || t === 'Flutter' || t === '全栈')).toBe(true);
	});
});
