import { describe, it, expect, vi } from 'vitest';
import {
	DEFAULT_RETRY_DELAYS_MS,
	RealtimeClient,
	type BrokerEvent,
	type BrokerEventHandler,
	type RealtimeTransport
} from '../lib/realtime';

// The Realtime module, driven through its injected transport port. No `EventSource`,
// no network, no bound port — which is the point of cutting the seam there.
//
// The regressions these cover: four hand-rolled subscribe blocks with inconsistent
// health gating, no retry, and `unsubscribe('*')` on a shared SDK singleton that tore
// down another page's handlers when one page left.

/** A transport that records subscriptions and can be told to fail. */
class FakeTransport implements RealtimeTransport {
	subscriptions: { collection: string; handler: BrokerEventHandler; stopped: boolean }[] = [];
	failNext = 0;

	subscribe(collection: string, handler: BrokerEventHandler): () => void {
		if (this.failNext > 0) {
			this.failNext -= 1;
			throw new Error('transport unavailable');
		}
		const entry = { collection, handler, stopped: false };
		this.subscriptions.push(entry);
		return () => {
			entry.stopped = true;
		};
	}

	emit(collection: string, event: BrokerEvent): void {
		for (const entry of this.subscriptions) {
			if (entry.collection === collection && !entry.stopped) entry.handler(event);
		}
	}

	get live(): number {
		return this.subscriptions.filter((s) => !s.stopped).length;
	}
}

function build(options: {
	transport: FakeTransport;
	healthy?: boolean | (() => boolean);
	retryDelaysMs?: number[];
	giveUp?: (collection: string, reason: string) => void;
}) {
	const sleeps: number[] = [];
	const healthy =
		typeof options.healthy === 'function' ? options.healthy : () => options.healthy !== false;
	const client = new RealtimeClient({
		transport: options.transport,
		isHealthy: async () => healthy(),
		retryDelaysMs: options.retryDelaysMs ?? [1, 1],
		sleep: async (ms: number) => {
			sleeps.push(ms);
		},
		onGiveUp: options.giveUp
	});
	return { client, sleeps };
}

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

const anEvent: BrokerEvent = { action: 'update', record: { id: 't1', status: 'running' } };

describe('Realtime health gating', () => {
	it('subscribes when the broker is reachable', async () => {
		const transport = new FakeTransport();
		const { client } = build({ transport, healthy: true });

		const seen: BrokerEvent[] = [];
		client.subscribeToCollection('automation_tasks', (e) => seen.push(e));
		await flush();

		expect(transport.live).toBe(1);
		transport.emit('automation_tasks', anEvent);
		expect(seen).toEqual([anEvent]);
	});

	it('does not subscribe into the void when the broker is unreachable', async () => {
		// Only the tasks page had a health gate; the others subscribed regardless and
		// silently never updated.
		const transport = new FakeTransport();
		const giveUp = vi.fn();
		const { client } = build({ transport, healthy: false, giveUp });

		client.subscribeToCollection('job_records', () => {});
		await flush();

		expect(transport.live).toBe(0);
		expect(giveUp).toHaveBeenCalledWith('job_records', 'broker unreachable');
	});

	it('recovers once the broker comes back, within the retry budget', async () => {
		// A transient hiccup used to stop the board from updating until a manual
		// refresh; here the first health check fails and the next one succeeds.
		const transport = new FakeTransport();
		let checks = 0;
		const { client, sleeps } = build({ transport, healthy: () => ++checks > 1 });

		const seen: BrokerEvent[] = [];
		client.subscribeToCollection('job_records', (e) => seen.push(e));
		for (let i = 0; i < 5; i++) await flush();

		expect(transport.live).toBe(1);
		expect(sleeps).toEqual([1]);
		expect(client.isConnected('job_records')).toBe(true);

		transport.emit('job_records', anEvent);
		expect(seen).toEqual([anEvent]);
	});

	it('retries a failed transport subscription rather than giving up immediately', async () => {
		const transport = new FakeTransport();
		transport.failNext = 1;
		const { client, sleeps } = build({ transport, healthy: true });

		client.subscribeToCollection('automation_tasks', () => {});
		await flush();
		await flush();

		expect(transport.live).toBe(1);
		expect(sleeps).toEqual([1]);
	});

	it('gives up after the bounded budget and reports why', async () => {
		const transport = new FakeTransport();
		const giveUp = vi.fn();
		const { client, sleeps } = build({ transport, healthy: true, giveUp });
		transport.failNext = DEFAULT_RETRY_DELAYS_MS.length + 5;

		client.subscribeToCollection('automation_tasks', () => {});
		for (let i = 0; i < 10; i++) await flush();

		expect(giveUp).toHaveBeenCalledWith('automation_tasks', 'transport subscribe failed');
		expect(sleeps.length).toBeLessThanOrEqual(2); // the test's own two-delay budget
	});
});

describe('Realtime subscription lifecycle', () => {
	it('shares one transport subscription across handlers', async () => {
		const transport = new FakeTransport();
		const { client } = build({ transport, healthy: true });

		client.subscribeToCollection('automation_tasks', () => {});
		client.subscribeToCollection('automation_tasks', () => {});
		await flush();

		expect(transport.live).toBe(1);
		expect(client.handlerCount('automation_tasks')).toBe(2);
	});

	it('removes only the handler that asked, never another page', async () => {
		// The `unsubscribe('*')` regression: leaving one page silenced another page's
		// live updates, so the nav badge and the jobs list disagreed.
		const transport = new FakeTransport();
		const { client } = build({ transport, healthy: true });

		const navBadge: BrokerEvent[] = [];
		const jobsList: BrokerEvent[] = [];
		const offNav = client.subscribeToCollection('automation_tasks', (e) => navBadge.push(e));
		client.subscribeToCollection('automation_tasks', (e) => jobsList.push(e));
		await flush();

		offNav();
		transport.emit('automation_tasks', anEvent);

		expect(navBadge).toEqual([]);
		expect(jobsList).toEqual([anEvent]);
		expect(transport.live).toBe(1);
	});

	it('drops the transport subscription when the last handler leaves', async () => {
		const transport = new FakeTransport();
		const idle = vi.fn();
		const client = new RealtimeClient({
			transport,
			isHealthy: async () => true,
			retryDelaysMs: [1],
			sleep: async () => {},
			onIdle: idle
		});

		const off = client.subscribeToCollection('job_records', () => {});
		await flush();
		off();

		expect(transport.live).toBe(0);
		expect(idle).toHaveBeenCalledWith('job_records');
	});

	it('tolerates a handler that throws, so the others still update', async () => {
		const transport = new FakeTransport();
		const { client } = build({ transport, healthy: true });

		const seen: BrokerEvent[] = [];
		client.subscribeToCollection('automation_tasks', () => {
			throw new Error('this page is broken');
		});
		client.subscribeToCollection('automation_tasks', (e) => seen.push(e));
		await flush();

		transport.emit('automation_tasks', anEvent);
		expect(seen).toEqual([anEvent]);
	});

	it('drops a subscription that arrives after everyone has left', async () => {
		const transport = new FakeTransport();
		const { client } = build({ transport, healthy: true });

		const off = client.subscribeToCollection('automation_tasks', () => {});
		off(); // leaves before the health check resolves
		await flush();

		expect(transport.live).toBe(0);
		expect(client.isConnected('automation_tasks')).toBe(false);
	});

	it('reset tears everything down', async () => {
		const transport = new FakeTransport();
		const { client } = build({ transport, healthy: true });

		client.subscribeToCollection('automation_tasks', () => {});
		client.subscribeToCollection('job_records', () => {});
		await flush();
		expect(transport.live).toBe(2);

		client.reset();
		expect(transport.live).toBe(0);
		expect(client.handlerCount('automation_tasks')).toBe(0);
	});
});
