// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/svelte';
import KeywordListEditor from '$lib/components/settings/KeywordListEditor.svelte';
import { validateCanBlacklistCompany } from '$lib/screening';

// 12 entries — past the default collapse threshold of 8.
const LONG_COMPANIES = [
	'深至科技',
	'某某劳务',
	'蓝海外包',
	'速达派遣',
	'远见人力',
	'卓越职介',
	'合众外包',
	'新程人力',
	'汇智派遣',
	'恒通服务',
	'智联外包',
	'创远人力'
];

function renderEditor(props: Record<string, unknown> = {}) {
	return render(KeywordListEditor, {
		props: {
			title: '公司黑名单 (Company Blacklist)',
			unit: '家',
			emptyText: '（暂无屏蔽企业）',
			placeholder: '输入需屏蔽的企业名称',
			items: [...LONG_COMPANIES],
			...props
		}
	});
}

async function openManager() {
	await fireEvent.click(screen.getByRole('button', { name: /管理全部/ }));
	return screen.getByTestId('keyword-editor-modal');
}

describe('KeywordListEditor isolated component', () => {
	beforeEach(() => {
		// nothing shared to seed — the component takes items as a bindable prop
	});

	afterEach(() => {
		cleanup();
	});

	it('keeps the compact chip view unchanged below the threshold', () => {
		renderEditor({ items: ['深至科技', '某某劳务'] });

		expect(screen.getByText('公司黑名单 (Company Blacklist)')).toBeTruthy();
		expect(screen.getByText('深至科技')).toBeTruthy();
		expect(screen.getByText('某某劳务')).toBeTruthy();
		expect(screen.getAllByTestId('keyword-chip')).toHaveLength(2);
		expect(screen.queryByRole('button', { name: /管理全部/ })).toBeNull();
		expect(screen.queryByRole('button', { name: /折叠/ })).toBeNull();
	});

	it('truncates the chip view past the threshold and shows the collapse counter', async () => {
		renderEditor();

		// jsdom performs no layout, so assert on the rendered chip count
		expect(screen.getAllByTestId('keyword-chip')).toHaveLength(8);
		expect(screen.queryByText('汇智派遣')).toBeNull();
		expect(screen.getByText(/\+4 家折叠/)).toBeTruthy();
		expect(screen.getByRole('button', { name: /管理全部/ })).toBeTruthy();

		await fireEvent.click(screen.getByRole('button', { name: /折叠/ }));
		expect(screen.getAllByTestId('keyword-chip')).toHaveLength(12);
		expect(screen.getByText('汇智派遣')).toBeTruthy();
	});

	it('opens the management modal listing every item and filters it by search', async () => {
		renderEditor();
		const modal = await openManager();

		expect(within(modal).getAllByTestId('keyword-editor-modal-item')).toHaveLength(12);
		expect(within(modal).getByText('共 12 家')).toBeTruthy();

		await fireEvent.input(within(modal).getByPlaceholderText('搜索关键词'), {
			target: { value: '派遣' }
		});

		const rows = within(modal).getAllByTestId('keyword-editor-modal-item');
		expect(rows).toHaveLength(2);
		expect(within(modal).getByText('速达派遣')).toBeTruthy();
		expect(within(modal).queryByText('深至科技')).toBeNull();
	});

	it('reports an empty search result instead of an empty list', async () => {
		renderEditor();
		const modal = await openManager();

		await fireEvent.input(within(modal).getByPlaceholderText('搜索关键词'), {
			target: { value: '不存在的关键词' }
		});

		expect(within(modal).queryAllByTestId('keyword-editor-modal-item')).toHaveLength(0);
		expect(within(modal).getByText(/没有匹配/)).toBeTruthy();
	});

	it('adds items from the modal through the onValidate guard', async () => {
		renderEditor({
			onValidate: (value: string) => validateCanBlacklistCompany(value, false)
		});
		const modal = await openManager();
		const addInput = within(modal).getByPlaceholderText('输入需屏蔽的企业名称（弹窗新增）');

		// masked / placeholder company names stay blocked by the 直招守卫
		await fireEvent.input(addInput, { target: { value: '某知名外企' } });
		await fireEvent.click(within(modal).getByRole('button', { name: '+ 添加' }));

		expect(within(modal).getByText(/属于保密\/占位公司名称/)).toBeTruthy();
		expect(within(modal).queryByText('某知名外企')).toBeNull();

		// a real直招企业 goes through
		await fireEvent.input(addInput, { target: { value: '  星环科技  ' } });
		await fireEvent.keyDown(addInput, { key: 'Enter' });

		expect(within(modal).getByText('星环科技')).toBeTruthy();
		expect(within(modal).getAllByTestId('keyword-editor-modal-item')).toHaveLength(13);
	});

	it('deletes a single item from the modal', async () => {
		renderEditor();
		const modal = await openManager();

		await fireEvent.click(within(modal).getAllByRole('button', { name: '移除' })[0]);

		expect(within(modal).getAllByTestId('keyword-editor-modal-item')).toHaveLength(11);
		expect(within(modal).queryByText('深至科技')).toBeNull();
		expect(within(modal).getByText('某某劳务')).toBeTruthy();
	});

	it('bulk clears the list behind a two-step confirmation', async () => {
		renderEditor();
		const modal = await openManager();

		const clearBtn = within(modal).getByRole('button', { name: '清空全部' });
		await fireEvent.click(clearBtn);
		expect(within(modal).getAllByTestId('keyword-editor-modal-item')).toHaveLength(12);

		await fireEvent.click(within(modal).getByRole('button', { name: '确认清空全部' }));
		expect(within(modal).queryAllByTestId('keyword-editor-modal-item')).toHaveLength(0);
		expect(within(modal).getByText('（暂无屏蔽企业）')).toBeTruthy();
	});

	it('closes the management modal on Escape and on backdrop click', async () => {
		renderEditor();
		const modal = await openManager();
		expect(modal).toBeTruthy();

		await fireEvent.keyDown(window, { key: 'Escape' });
		expect(screen.queryByTestId('keyword-editor-modal')).toBeNull();

		await openManager();
		await fireEvent.click(screen.getByTestId('keyword-editor-modal-backdrop'));
		expect(screen.queryByTestId('keyword-editor-modal')).toBeNull();
	});

	it('never renders a button that would submit the surrounding settings form', async () => {
		renderEditor();
		await openManager();

		const buttons = document.querySelectorAll('button');
		expect(buttons.length).toBeGreaterThan(0);
		buttons.forEach((btn) => {
			expect(btn.getAttribute('type')).toBe('button');
		});
	});
});