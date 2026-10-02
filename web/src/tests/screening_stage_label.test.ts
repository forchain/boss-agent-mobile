import { describe, it, expect } from 'vitest';
import { getScreeningStageLabel } from '$lib/screening';

describe('getScreeningStageLabel', () => {
	it('labels a full-JD deep-screener rejection as 精筛淘汰, not 初筛淘汰', () => {
		// The Qoder case: the card digest never carried the Java requirement, so a
		// card-stage label made the rejection look like it came from evidence the card
		// stage never saw.
		expect(getScreeningStageLabel('filtered_by_deep_screener')).toBe('精筛淘汰');
	});

	it('labels card-stage rejections as 初筛淘汰', () => {
		expect(getScreeningStageLabel('filtered_by_keyword')).toBe('初筛淘汰');
		expect(getScreeningStageLabel('filtered_by_app_rule')).toBe('初筛淘汰');
	});

	it('labels detail-stage App filters distinctly from both screening stages', () => {
		expect(getScreeningStageLabel('detail_app_rule')).toBe('详情页过滤');
	});

	it('labels an expired posting as 岗位已失效', () => {
		expect(getScreeningStageLabel('expired_posting')).toBe('岗位已失效');
	});

	it('falls back to 已忽略 for legacy or manual records with no stage', () => {
		expect(getScreeningStageLabel('')).toBe('已忽略');
		expect(getScreeningStageLabel(null)).toBe('已忽略');
		expect(getScreeningStageLabel(undefined)).toBe('已忽略');
		expect(getScreeningStageLabel('something_unknown')).toBe('已忽略');
	});
});
