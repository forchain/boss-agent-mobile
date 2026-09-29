/**
 * The dashboard's shared Realtime client.
 *
 * Lives apart from `realtime.ts` so the module under test keeps no import of the
 * PocketBase SDK: the client is pure, and this file is the wiring that gives it a
 * transport and a health probe.
 *
 * The SDK client is built lazily, on the first `dashboardRealtime()` call, and
 * repointed whenever the configured broker URL changes. Building it at import time
 * meant the live stream connected to the fallback `http://<host>:8090` — the URL
 * `getPocketBaseUrl()` returned before the layout had run `setPocketBaseUrl` — while
 * every REST call went to the configured broker. One database, two origins, and a
 * dashboard opened anywhere that cannot reach :8090 silently stopped streaming task
 * logs with no error anywhere to follow.
 */

import PocketBase from 'pocketbase';
import { checkPocketBaseHealth, getPocketBaseUrl } from '$lib/pocketbase';
import { RealtimeClient, createPocketBaseTransport } from '$lib/realtime';

// The SDK instance lives here, and only here. `pocketbase.ts` does not import it, so
// the browser's data path cannot fall back to a cross-origin SDK call: the one
// direct-to-broker consumer is this live-update stream, which ADR 0006 sanctions.
let pb: InstanceType<typeof PocketBase> | null = null;
let pbUrl = '';
let shared: RealtimeClient | null = null;

function clientForConfiguredUrl(): InstanceType<typeof PocketBase> {
	const url = getPocketBaseUrl();
	if (!pb) {
		pb = new PocketBase(url);
		pbUrl = url;
	} else if (url !== pbUrl) {
		// The SDK reads `baseURL` when it builds each request, so an already-open
		// EventSource keeps its old origin until the SDK's own reconnect, while every
		// new subscription goes to the configured broker.
		pb.baseURL = url;
		pbUrl = url;
	}
	return pb;
}

export function dashboardRealtime(): RealtimeClient {
	// Resolve (or repoint) the SDK client on every access, so the live stream follows
	// the configured broker even when the layout learns that URL after first mount.
	clientForConfiguredUrl();
	if (!shared) {
		shared = new RealtimeClient({
			transport: createPocketBaseTransport({
				collection: (name: string) => clientForConfiguredUrl().collection(name)
			}),
			isHealthy: () => checkPocketBaseHealth(),
			onGiveUp: (collection, reason) => {
				console.warn(`[realtime] subscriptions for ${collection} abandoned: ${reason}`);
			}
		});
	}
	return shared;
}

/** Stop every live subscription. For teardown in tests. */
export function resetDashboardRealtime(): void {
	shared?.reset();
	shared = null;
	pb = null;
	pbUrl = '';
}
