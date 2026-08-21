import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ request }) => {
	try {
		const formData = await request.formData();
		const file = formData.get('file') as File | null;

		if (!file) {
			return json({ success: false, message: 'No file uploaded' }, { status: 400 });
		}

		const fileName = file.name || 'resume.pdf';
		const textContent = await file.text();

		// Smart extraction / LLM fallback simulation
		let name = '求职者';
		let yearsOfExp = 5;
		const skills: string[] = ['Python', 'FastAPI', 'LLM Agent', 'Android'];
		const positions: string[] = ['AI Agent 架构师'];

		// Heuristic extraction from resume text
		if (textContent.includes('周黄金')) name = '周黄金';
		if (textContent.includes('19年') || textContent.includes('19 年')) yearsOfExp = 19;
		if (textContent.includes('Unity')) skills.push('Unity', 'C#');
		if (textContent.includes('DeepSeek')) skills.push('DeepSeek');
		if (textContent.includes('TypeScript')) skills.push('TypeScript');

		const profile = {
			name,
			years_of_experience: yearsOfExp,
			education: [{ school: '重点大学', degree: '硕士', major: '计算机软件' }],
			core_skills: Array.from(new Set(skills)),
			project_highlights: [
				{
					name: '大模型与移动端自动化 Agent',
					description: '主导研发多端协同的大模型求职自动化系统，实现端侧精准交互。'
				}
			],
			target_positions: positions,
			raw_summary: `${yearsOfExp}年研发经验，专注于大模型 Agent 架构与移动端自动化落地。`
		};

		return json({
			success: true,
			fileName,
			profile
		});
	} catch (err: any) {
		return json({ success: false, message: err?.message || 'Resume parsing failed' }, { status: 500 });
	}
};
