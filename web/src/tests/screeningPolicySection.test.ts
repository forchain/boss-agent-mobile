// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/svelte';
import ScreeningPolicySection from '$lib/components/settings/ScreeningPolicySection.svelte';
import type { ScreeningPolicy } from '$lib/types';
import { screeningPolicyStore, maxCommuteInputStore } from '$lib/stores/settings';

const SAMPLE_POLICY: ScreeningPolicy = {
	enable_screening: true,
	title_whitelist: ['大模型', 'Agent'],
	title_blacklist: ['实习', '销售'],
	company_blacklist: ['深至科技'],
	jd_blacklist: ['大小周'],
	business_district_blacklist: ['崇明区'],
	business_district_inspect_list: ['张江'],
	max_commute_distance_km: 30
};

describe('ScreeningPolicySection isolated component', () => {
	beforeEach(() => {
		screeningPolicyStore.set({ ...SAMPLE_POLICY });
		maxCommuteInputStore.set('30');
	});

	afterEach(() => {
		cleanup();
	});

	it('renders in isolation with initial policy facets and commute limit', () => {
		render(ScreeningPolicySection, {
			props: { policy: SAMPLE_POLICY }
		});

		expect(screen.getByText('初筛与黑白名单策略 (Screening Policy)')).toBeTruthy();
		expect(screen.getAllByText('大模型').length).toBeGreaterThanOrEqual(1);
		expect(screen.getByText('Agent')).toBeTruthy();
		expect(screen.getByText('实习')).toBeTruthy();
		expect(screen.getByText('深至科技')).toBeTruthy();
		expect(screen.getByText('大小周')).toBeTruthy();
		expect(screen.getByDisplayValue('30')).toBeTruthy();
	});

	it('adds a new item to title whitelist via input', async () => {
		render(ScreeningPolicySection, {
			props: { policy: { ...SAMPLE_POLICY, title_whitelist: ['大模型'] } }
		});

		const input = screen.getByPlaceholderText('输入强意向领域，如: 大模型');
		await fireEvent.input(input, { target: { value: 'Prompt Engineer' } });
		await fireEvent.keyDown(input, { key: 'Enter' });

		expect(screen.getByText('Prompt Engineer')).toBeTruthy();
	});

	it('prevents adding protected or masked company names to company blacklist', async () => {
		render(ScreeningPolicySection, {
			props: { policy: SAMPLE_POLICY }
		});

		const companyInput = screen.getByPlaceholderText('输入需屏蔽的企业名称');
		await fireEvent.input(companyInput, { target: { value: '某知名外企' } });
		await fireEvent.keyDown(companyInput, { key: 'Enter' });

		expect(screen.getByText(/属于保密\/占位公司名称/)).toBeTruthy();
		expect(screen.queryByText('某知名外企')).toBeNull();
	});

	it('disables commute limit when clicking the disable button', async () => {
		render(ScreeningPolicySection, {
			props: { policy: SAMPLE_POLICY }
		});

		const disableBtn = screen.getByRole('button', { name: /不限 \/ 禁用/ });
		await fireEvent.click(disableBtn);

		expect(screen.getByText(/已禁用通勤筛选/)).toBeTruthy();
	});

	it('invokes custom onSave callback with updated policy and commute ceiling', async () => {
		const onSave = vi.fn();
		render(ScreeningPolicySection, {
			props: { policy: SAMPLE_POLICY, onSave }
		});

		const saveBtn = screen.getByRole('button', { name: /单独保存初筛策略/ });
		await fireEvent.click(saveBtn);

		expect(onSave).toHaveBeenCalledTimes(1);
		const saved = onSave.mock.calls[0][0];
		expect(saved.title_whitelist).toContain('大模型');
		expect(saved.max_commute_distance_km).toBe(30);
	});
});
