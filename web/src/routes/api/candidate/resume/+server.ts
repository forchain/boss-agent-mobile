import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { runPythonScript, getProjectRoot } from '$lib/server/pythonRunner';
import path from 'path';
import fs from 'fs';
import os from 'os';

export const POST: RequestHandler = async ({ request }) => {
	let tempFilePath: string | null = null;
	try {
		const formData = await request.formData();
		const file = formData.get('file') as File | null;
		const llmSettingsStr = (formData.get('llmSettings') as string) || '';

		if (!file) {
			return json({ success: false, message: '请上传有效的简历文件' }, { status: 400 });
		}

		const fileName = file.name || 'resume.pdf';
		const ext = path.extname(fileName).toLowerCase() || '.pdf';

		// Create upload directory
		const projectRoot = getProjectRoot();
		const uploadDir = path.join(projectRoot, '.boss_agent', 'uploads');
		if (!fs.existsSync(uploadDir)) {
			fs.mkdirSync(uploadDir, { recursive: true });
		}

		const safeTempName = `resume_${Date.now()}_${Math.random().toString(36).substring(2, 8)}${ext}`;
		tempFilePath = path.join(uploadDir, safeTempName);

		// Write uploaded file buffer
		const buffer = Buffer.from(await file.arrayBuffer());
		fs.writeFileSync(tempFilePath, buffer);

		const args = ['--file', tempFilePath];
		if (llmSettingsStr) {
			args.push('--llm-config', llmSettingsStr);
		}

		console.log(`[ResumeAPI] Received file upload: ${fileName} (${buffer.length} bytes), temp path: ${tempFilePath}`);
		const { stdout, stderr, code } = await runPythonScript('scripts/parse_resume.py', args);

		// Parse the JSON output from stdout
		let parsedResult: any = null;
		if (stdout) {
			const jsonMatch = stdout.match(/\{[\s\S]*\}/);
			if (jsonMatch) {
				try {
					parsedResult = JSON.parse(jsonMatch[0]);
				} catch (e) {}
			}
		}

		if (parsedResult && parsedResult.success && parsedResult.profile) {
			console.log(`[ResumeAPI] Successfully parsed resume for: ${parsedResult.profile.name}`);
			return json({
				success: true,
				fileName,
				profile: parsedResult.profile,
				message: parsedResult.message || '简历解析成功'
			});
		} else {
			const errMsg = parsedResult?.message || stderr || '大模型解析简历失败，请检查文件格式或大模型配置';
			console.error(`[ResumeAPI] Resume parsing failed (code: ${code}): ${errMsg}`);
			return json({
				success: false,
				message: errMsg
			}, { status: 500 });
		}
	} catch (err: any) {
		console.error(`[ResumeAPI] Exception occurred: ${err?.message || err}`);
		return json({
			success: false,
			message: err?.message || '简历解析发生未知异常'
		}, { status: 500 });
	} finally {
		if (tempFilePath && fs.existsSync(tempFilePath)) {
			try {
				fs.unlinkSync(tempFilePath);
			} catch (e) {}
		}
	}
};
