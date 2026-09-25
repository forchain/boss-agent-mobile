/**
 * The dashboard's one realtime module.
 *
 * PocketBase SSE is the frontend's live channel (ADR 0006 names it explicitly), so this
 * is the one honest direct-to-broker path — everything else goes through the BFF. What
 * it replaces was four hand-rolled `subscribe`/`unsubscribe` blocks (layout, tasks,
 * jobs, searches) with inconsistent health gating, no retry, and `unsubscribe('*')`
 * calls on a shared SDK singleton that tore down *another page's* handlers as a
 * side-effect of leaving one.
 *
 * The three properties it exists to provide:
 *
 * 1. **A health gate before subscribing.** Only the tasks page had one; the others
 *    subscribed into the void and silently never updated.
 * 2. **Bounded retry.** A transient broker hiccup used to stop the board from updating
 *    until a manual refresh.
 * 3. **Per-handler unsubscription.** The handle returned by `subscribeToCollection`
 *    removes exactly the handler that asked for it, and never anyone else's.
 *
 * The transport is injected, so tests drive it with a fake port — no `EventSource`, no
 * network, no bound port.
 */

/** One broker event, narrowed to what the dashboard reads. */
export interface BrokerEvent {
	action: 'create' | 'update' | 'delete';
	record: Record<string, any>;
}

export type BrokerEventHandler = (event: BrokerEvent) => void;

/**
 * The port this module is cut at: something that can subscribe to a collection and
 * hand back a way to stop.
 */
export interface RealtimeTransport {
	subscribe(collection: string, handler: BrokerEventHandler): () => void;
}

export interface RealtimeOptions {
	/** Establishes a subscription. Injected so tests never touch the network. */
	transport: RealtimeTransport;
	/** Whether the broker is reachable. Called before every (re)subscription. */
	isHealthy: () => Promise<boolean>;
	/** Delays between attempts, in order, when the health gate or the transport fails. */
	retryDelaysMs?: number[];
	sleep?: (ms: number) => Promise<void>;
	/** Called after every consumer of a collection has unsubscribed. */
	onIdle?: (collection: string) => void;
	/** Called when an attempt is abandoned, so a caller can surface an offline state. */
	onGiveUp?: (collection: string, reason: string) => void;
}

/** The delay before each attempt, in order. Bounded so a dead broker cannot spin. */
export const DEFAULT_RETRY_DELAYS_MS = [500, 1000, 2000, 5000, 10_000];

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export class RealtimeClient {
	private readonly handlersByCollection = new Map<string, Set<BrokerEventHandler>>();
	private readonly transportStops = new Map<string, () => void>();
	private readonly options: RealtimeOptions;

	constructor(options: RealtimeOptions) {
		this.options = options;
	}

	/**
	 * Subscribe to a collection's events.
	 *
	 * Returns the handle that removes *this* handler. There is deliberately no
	 * `unsubscribeAll`: the whole point of the handle is that one page leaving cannot
	 * silence another.
	 */
	subscribeToCollection(collection: string, handler: BrokerEventHandler): () => void {
		const handlers = this.handlersByCollection.get(collection) ?? new Set<BrokerEventHandler>();
		this.handlersByCollection.set(collection, handlers);

		const wasEmpty = handlers.size === 0;
		handlers.add(handler);
		if (wasEmpty) {
			void this.connect(collection);
		}

		return () => this.removeHandler(collection, handler);
	}

	/** How many handlers currently hold a collection open. For tests and diagnostics. */
	handlerCount(collection: string): number {
		return this.handlersByCollection.get(collection)?.size ?? 0;
	}

	/** Whether a live transport subscription is currently held for a collection. */
	isConnected(collection: string): boolean {
		return this.transportStops.has(collection);
	}

	private removeHandler(collection: string, handler: BrokerEventHandler): void {
		const handlers = this.handlersByCollection.get(collection);
		if (!handlers) return;
		handlers.delete(handler);
		if (handlers.size > 0) return;

		this.handlersByCollection.delete(collection);
		this.dropTransport(collection);
	}

	private dropTransport(collection: string): void {
		const stop = this.transportStops.get(collection);
		if (!stop) return;
		this.transportStops.delete(collection);
		try {
			stop();
		} catch {
			// A transport that throws on teardown must not strand the collection.
		}
		this.options.onIdle?.(collection);
	}

	/**
	 * Establish the transport subscription, health-gating and retrying within budget.
	 *
	 * Each attempt re-reads the handler set, so a handler that arrived (or left) while a
	 * retry was pending is honoured rather than frozen into the attempt.
	 */
	private async connect(collection: string): Promise<void> {
		const delays = this.options.retryDelaysMs ?? DEFAULT_RETRY_DELAYS_MS;
		const sleep = this.options.sleep ?? defaultSleep;

		for (let attempt = 0; ; attempt++) {
			if (this.handlerCount(collection) === 0) return; // everyone left meanwhile

			const healthy = await this.options.isHealthy().catch(() => false);
			if (healthy) {
				const stop = this.transportSubscribe(collection);
				if (stop) {
					if (this.handlerCount(collection) === 0) {
						// The last handler left while we were awaiting: do not leak the
						// subscription we just made.
						try {
							stop();
						} catch {
							// ignore
						}
					} else {
						this.transportStops.set(collection, stop);
					}
					return;
				}
			}

			if (attempt >= delays.length) {
				this.options.onGiveUp?.(
					collection,
					healthy ? 'transport subscribe failed' : 'broker unreachable'
				);
				return;
			}
			await sleep(delays[attempt]);
		}
	}

	private transportSubscribe(collection: string): (() => void) | null {
		try {
			return this.options.transport.subscribe(collection, (event) => {
				// A copy: a handler may unsubscribe itself while being notified.
				for (const handler of [...(this.handlersByCollection.get(collection) ?? [])]) {
					try {
						handler(event);
					} catch {
						// One page's handler throwing must not silence the others.
					}
				}
			});
		} catch {
			return null;
		}
	}

	/** Drop every subscription and transport handle. For teardown and tests. */
	reset(): void {
		for (const collection of [...this.transportStops.keys()]) {
			this.dropTransport(collection);
		}
		this.handlersByCollection.clear();
	}
}

/**
 * The production transport: PocketBase's own SSE.
 *
 * Each subscription gets a *unique topic*, so the handle it returns unsubscribes
 * exactly that one. The plain `unsubscribe('*')` the pages used before removed every
 * handler on the collection — including another page's, which is how leaving the jobs
 * page used to silence the nav badge.
 */
export function createPocketBaseTransport(
	pocketBase: {
		collection: (name: string) => {
			subscribe: (topic: string, handler: (event: any) => void) => Promise<void> | void;
			unsubscribe: (topic: string) => Promise<void> | void;
		};
	}
): RealtimeTransport {
	let topicCounter = 0;
	return {
		subscribe(collection, handler) {
			const topic = `realtime-${++topicCounter}`;
			void Promise.resolve(
				pocketBase.collection(collection).subscribe(topic, (event) => {
					handler({ action: event?.action, record: event?.record ?? {} });
				})
			).catch(() => {
				// A refused subscription is the health gate's business, not a throw here.
			});
			return () => {
				try {
					void Promise.resolve(pocketBase.collection(collection).unsubscribe(topic)).catch(
						() => {}
					);
				} catch {
					// ignore
				}
			};
		}
	};
}
