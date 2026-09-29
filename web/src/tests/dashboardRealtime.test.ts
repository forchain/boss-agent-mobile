import { describe, it, expect, vi, beforeEach } from 'vitest';

// The wiring module gets its own coverage because its bug was invisible to the pure
// RealtimeClient tests: the PocketBase SDK instance used to be built at import time,
// before the layout ever called `setPocketBaseUrl(data.pocketbaseUrl)`. The live
// stream then connected to the fallback `http://<host>:8090` while every REST call
// went to the configured broker — two origins for one database, and a dashboard
// opened anywhere that cannot reach :8090 silently stops streaming task logs.

const instances: { baseURL: string }[] = [];

vi.mock('pocketbase', () => {
	return {
		default: class FakePocketBase {
			baseURL: string;
			constructor(url: string) {
				this.baseURL = url;
				instances.push(this);
			}
			collection() {
				return {
					subscribe: vi.fn().mockResolvedValue(vi.fn()),
					unsubscribe: vi.fn()
				};
			}
		}
	};
});

let configuredUrl = 'http://localhost:8090';

vi.mock('$lib/pocketbase', () => ({
	getPocketBaseUrl: () => configuredUrl,
	checkPocketBaseHealth: async () => true
}));

describe('dashboardRealtime wiring', () => {
	beforeEach(async () => {
		instances.length = 0;
		configuredUrl = 'http://localhost:8090';
		const mod = await import('$lib/dashboardRealtime');
		mod.resetDashboardRealtime();
	});

	it('builds the SDK client lazily, on the URL configured at first use', async () => {
		const { dashboardRealtime } = await import('$lib/dashboardRealtime');
		configuredUrl = 'https://pocketbase.chainer.tech:4433';

		dashboardRealtime();

		expect(instances.map((i) => i.baseURL)).toEqual(['https://pocketbase.chainer.tech:4433']);
	});

	it('repoints the shared client when the configured URL changes', async () => {
		const { dashboardRealtime } = await import('$lib/dashboardRealtime');
		const first = dashboardRealtime();
		configuredUrl = 'https://pocketbase.chainer.tech:4433';

		const second = dashboardRealtime();

		expect(second).toBe(first);
		expect(instances).toHaveLength(1);
		expect(instances[0].baseURL).toBe('https://pocketbase.chainer.tech:4433');
	});

	it('keeps one client while the URL is unchanged', async () => {
		const { dashboardRealtime } = await import('$lib/dashboardRealtime');
		expect(dashboardRealtime()).toBe(dashboardRealtime());
		expect(instances).toHaveLength(1);
	});
});
