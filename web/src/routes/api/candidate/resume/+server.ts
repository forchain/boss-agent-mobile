import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { runPythonScript, getProjectRoot } from '$lib/server/pythonRunner';
import { createRevision, listRevisions } from '$lib/server/collections';
import { BrokerError } from '$lib/server/broker';
import { sanitizeLlmSettingsForRunner } from '$lib/server/settings';
import path from 'path';
import fs from 'fs';
import os from 'os';

export const GET: RequestHandler = async ({ url }) => {
	try {
		const userId = url.searchParams.get('userId') || 'default';
		return json({ success: true, revisions: await listRevisions(userId) });
	} catch (e: any) {
		return json(
			{ success: false, message: e?.message || 'Failed to list resume revisions', revisions: [] },
			{ status: e instanceof BrokerError ? e.status : 500 }
		);
	}
};

export const POST: RequestHandler = async ({ request }) => {
	// Two shapes, one endpoint: multipart parses an uploaded résumé (below), and a JSON
	// body records a revision the caller already has the text for. A separate
	// `/revision` path would collide with the `[id]` segment.
	// JSON is opted into explicitly; anything else is treated as an upload. Probing for
	// "not multipart" instead would misread a request whose headers are absent — which is
	// exactly the shape a route-level test's stub request has.
	const contentType = request.headers?.get?.('content-type') || '';
	if (contentType.includes('application/json')) {
		try {
			const body = await request.json().catch(() => ({}));
			const userId = body.userId || body.user_id || 'default';
			return json({ success: true, revision: await createRevision(body, userId) });
		} catch (e: any) {
			return json(
				{ success: false, message: e?.message || 'Failed to record resume revision' },
				{ status: e instanceof BrokerError ? e.status : 500 }
			);
		}
	}

	let tempFilePath: string | null = null;
	try {
		const formData = await request.formData();
		const file = (formData.get('file') || formData.get('resume')) as File | null;
		const llmSettingsStr = (formData.get('llmSettings') as string) || '';
		const mergeMode = (formData.get('mergeMode') as string) || (formData.get('merge_mode') as string) || '';
		const awaitReview = (formData.get('awaitReview') as string) === 'true' || (formData.get('await_review') as string) === 'true';
		const userId = (formData.get('userId') as string) || (formData.get('user_id') as string) || 'default';

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

		const args = ['--file', tempFilePath, '--file-name', fileName, '--user-id', userId];
		if (llmSettingsStr) {
			try {
				const parsedLlm = JSON.parse(llmSettingsStr);
				const cleanedLlm = sanitizeLlmSettingsForRunner(parsedLlm);
				args.push('--llm-config', JSON.stringify(cleanedLlm));
			} catch {
				args.push('--llm-config', llmSettingsStr);
			}
		}
		if (mergeMode) {
			args.push('--merge-mode', mergeMode);
		}
		if (awaitReview) {
			args.push('--await-review');
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
				diff_summary: parsedResult.diff_summary || '',
				status: parsedResult.status || 'completed',
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
