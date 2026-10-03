// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/svelte';
import JobIgnoredBanner from '$lib/components/studio/JobIgnoredBanner.svelte';
import JobHeaderCard from '$lib/components/studio/JobHeaderCard.svelte';
import JobMatchEvaluationPanel from '$lib/components/studio/JobMatchEvaluationPanel.svelte';
import JobGreetingRefinementPanel from '$lib/components/studio/JobGreetingRefinementPanel.svelte';
import JobActionsBar from '$lib/components/studio/JobActionsBar.svelte';
import JobScreeningStudioPanel from '$lib/components/studio/JobScreeningStudioPanel.svelte';
import type { JobRecord } from '$lib/types';

const BASE_JOB: JobRecord = {
	id: 'job_1',
	fingerprint: 'fp_1',
	title: '高级 Python 研发工程师',
	company_name: '测试智能科技有限公司',
	recruiter_name: '张经理',
	recruiter_title: '技术合伙人',
	salary_range: '30-50K',
	location: '上海·张江高科',
	status: 'jd_saved',
	job_description: '负责核心分布式任务调度与高并发设计。',
	tags: ['Python', 'FastAPI', 'Redis'],
	is_headhunter: false
};

afterEach(() => {
	cleanup();
});

describe('JobIgnoredBanner (Issue #318)', () => {
	it('renders warning banner with reason and restore button when status is ignored', async () => {
		const onRestore = vi.fn();
		const ignoredJob: JobRecord = {
			...BASE_JOB,
			status: 'ignored',
			screened_reason: '工作年限不符合'
		};
		render(JobIgnoredBanner, {
			props: { job: ignoredJob, onRestore }
		});

		expect(screen.getByText(/此岗位已被初步筛选淘汰/)).toBeTruthy();
		expect(screen.getByText(/工作年限不符合/)).toBeTruthy();
		// Label reads 恢复为有效候选职位 since #347: the banner now offers a re-screen
		// button beside restore, so "恢复此职位" alone no longer says which is which.
		const restoreBtn = screen.getByRole('button', { name: /恢复为有效候选职位/ });
		await fireEvent.click(restoreBtn);
		expect(onRestore).toHaveBeenCalledTimes(1);
	});

	it('renders nothing when status is not ignored', () => {
		render(JobIgnoredBanner, {
			props: { job: BASE_JOB, onRestore: () => {} }
		});
		expect(screen.queryByText(/此岗位已被初步筛选淘汰/)).toBeNull();
	});
});

describe('JobHeaderCard (Issue #318)', () => {
	it('renders job title, company, recruiter and salary information', () => {
		render(JobHeaderCard, {
			props: { job: BASE_JOB }
		});

		expect(screen.getByText('高级 Python 研发工程师')).toBeTruthy();
		expect(screen.getByText('测试智能科技有限公司')).toBeTruthy();
		expect(screen.getByText(/张经理/)).toBeTruthy();
		expect(screen.getByText('30-50K')).toBeTruthy();
		expect(screen.getByText('🏢 企业直招')).toBeTruthy();
		expect(screen.getByText('🏷️ Python')).toBeTruthy();
	});

	it('triggers onDelete callback when delete button is clicked', async () => {
		const onDelete = vi.fn();
		render(JobHeaderCard, {
			props: { job: BASE_JOB, onDelete }
		});

		const deleteBtn = screen.getByRole('button', { name: /删除职位/ });
		await fireEvent.click(deleteBtn);
		expect(onDelete).toHaveBeenCalledWith(BASE_JOB);
	});
});

describe('JobMatchEvaluationPanel (Issue #318)', () => {
	it('renders un-evaluated prompt when status is jd_saved without score', async () => {
		const onEvaluate = vi.fn();
		render(JobMatchEvaluationPanel, {
			props: { job: BASE_JOB, onEvaluate }
		});

		expect(screen.getByText(/该岗位已入库，尚未执行匹配分析/)).toBeTruthy();
		const evalBtn = screen.getByRole('button', { name: /开始 AI 匹配度评估/ });
		await fireEvent.click(evalBtn);
		expect(onEvaluate).toHaveBeenCalledTimes(1);
	});

	it('renders match score and requirements when evaluated', () => {
		const evaluatedJob: JobRecord = {
			...BASE_JOB,
			status: 'matched',
			match_score: 92,
			jd_key_requirements: ['精通分布式调度', '熟悉并发性能优化']
		};
		render(JobMatchEvaluationPanel, {
			props: { job: evaluatedJob, onEvaluate: () => {} }
		});

		expect(screen.getByText('92 / 100')).toBeTruthy();
		expect(screen.getByText('精通分布式调度')).toBeTruthy();
		expect(screen.getByText('熟悉并发性能优化')).toBeTruthy();
		expect(screen.getByRole('button', { name: /重新评估契合度/ })).toBeTruthy();
	});
});

describe('JobGreetingRefinementPanel (Issue #318)', () => {
	it('renders greeting textarea and handles save greeting', async () => {
		const onSaveGreeting = vi.fn();
		render(JobGreetingRefinementPanel, {
			props: {
				customGreeting: '您好，我对该架构师岗位非常感兴趣。',
				critiqueInput: '',
				onSaveGreeting,
				onDismissSuggestion: () => {},
				onRefineFromManualEdit: () => {},
				onRefineGreeting: () => {},
				onDismissDiff: () => {},
				onApplyRevisedGreeting: () => {},
				onDismissPromptRefinement: () => {},
				onConfirmAdoptPrompt: () => {}
			}
		});

		expect(screen.getByDisplayValue('您好，我对该架构师岗位非常感兴趣。')).toBeTruthy();
		const saveBtn = screen.getByRole('button', { name: /保存修改/ });
		await fireEvent.click(saveBtn);
		expect(onSaveGreeting).toHaveBeenCalledTimes(1);
	});

	it('renders refinement diff comparison when provided', () => {
		render(JobGreetingRefinementPanel, {
			props: {
				customGreeting: '初版招呼语',
				critiqueInput: '突出海外背景',
				refinementDiff: { before: '初版招呼语', after: '优化后的新招呼语' },
				onSaveGreeting: () => {},
				onDismissSuggestion: () => {},
				onRefineFromManualEdit: () => {},
				onRefineGreeting: () => {},
				onDismissDiff: () => {},
				onApplyRevisedGreeting: () => {},
				onDismissPromptRefinement: () => {},
				onConfirmAdoptPrompt: () => {}
			}
		});

		expect(screen.getByText(/招呼语微调效果对比/)).toBeTruthy();
		expect(screen.getByText('初版招呼语')).toBeTruthy();
		expect(screen.getByText('优化后的新招呼语')).toBeTruthy();
		expect(screen.getByRole('button', { name: /采纳并应用/ })).toBeTruthy();
	});
});

describe('JobActionsBar (Issue #318)', () => {
	it('renders apply, ignore, and blacklist action buttons', async () => {
		const onDispatchApply = vi.fn();
		const onIgnore = vi.fn();
		const onBlacklist = vi.fn();

		render(JobActionsBar, {
			props: {
				job: BASE_JOB,
				blacklistGuardrail: { allowed: true, notice: '' },
				onRestore: () => {},
				onClearCommunication: () => {},
				onClearCompanyCommunication: () => {},
				onDispatchApply,
				onIgnore,
				onBlacklist
			}
		});

		const applyBtn = screen.getByRole('button', { name: /立即发起移动端打招呼/ });
		await fireEvent.click(applyBtn);
		expect(onDispatchApply).toHaveBeenCalledTimes(1);

		const ignoreBtn = screen.getByRole('button', { name: /仅忽略此职位/ });
		await fireEvent.click(ignoreBtn);
		expect(onIgnore).toHaveBeenCalledTimes(1);

		const blacklistBtn = screen.getByRole('button', { name: /屏蔽该公司/ });
		await fireEvent.click(blacklistBtn);
		expect(onBlacklist).toHaveBeenCalledTimes(1);
	});

	it('renders clear communication button when job is applied', async () => {
		const onClearCommunication = vi.fn();
		const appliedJob: JobRecord = { ...BASE_JOB, status: 'applied' };

		render(JobActionsBar, {
			props: {
				job: appliedJob,
				blacklistGuardrail: { allowed: true, notice: '' },
				onRestore: () => {},
				onClearCommunication,
				onClearCompanyCommunication: () => {},
				onDispatchApply: () => {},
				onIgnore: () => {},
				onBlacklist: () => {}
			}
		});

		const clearBtn = screen.getByRole('button', { name: /清除沟通状态/ });
		await fireEvent.click(clearBtn);
		expect(onClearCommunication).toHaveBeenCalledTimes(1);
	});
});

describe('JobScreeningStudioPanel (Spec #340, Issue #347)', () => {
	const BASE_PROPS = {
		critiqueInput: '',
		onRestore: () => {},
		onRetest: () => {},
		onEvaluate: () => {},
		onAdoptAndRefinePrompt: () => {},
		onDismissPromptRefinement: () => {},
		onConfirmAdoptPrompt: () => {}
	};

	it('renders the deep screening drawer and gates retest on a critique', async () => {
		const onRetest = vi.fn();
		render(JobScreeningStudioPanel, { props: { ...BASE_PROPS, onRetest } });

		expect(screen.getByText(/精筛复核、纠偏重测与提示词自愈/)).toBeTruthy();

		// Retest stays disabled until an operator states why the verdict was wrong:
		// a blind retest is the same verdict asked twice.
		const retestBtn = screen.getByRole('button', { name: /重新裁决/ });
		expect((retestBtn as HTMLButtonElement).disabled).toBe(true);
	});

	it('runs an objective re-screen through the evaluate callback', async () => {
		const onEvaluate = vi.fn();
		render(JobScreeningStudioPanel, { props: { ...BASE_PROPS, onEvaluate } });

		const evaluateBtn = screen.getByRole('button', { name: /依据当前提示词重新精筛/ });
		await fireEvent.click(evaluateBtn);
		expect(onEvaluate).toHaveBeenCalled();
	});

	it('surfaces the objective verdict with its stage label, and restores on approval', async () => {
		const onRestore = vi.fn();
		render(JobScreeningStudioPanel, {
			props: {
				...BASE_PROPS,
				onRestore,
				evaluateVerdict: { approved: true, reason: '未命中黑名单', stage: 'filtered_by_deep_screener' }
			}
		});

		expect(screen.getByText(/精筛客观裁决：合格保留 \(Approved\)/)).toBeTruthy();
		expect(screen.queryByText(/精筛客观裁决：维持淘汰/)).toBeNull();
		expect(screen.getByText('精筛淘汰')).toBeTruthy();

		const restoreBtn = screen.getByRole('button', { name: /恢复为有效候选/ });
		await fireEvent.click(restoreBtn);
		expect(onRestore).toHaveBeenCalledTimes(1);
	});
});
