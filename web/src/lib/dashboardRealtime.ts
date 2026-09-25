/**
 * The dashboard's shared Realtime client.
 *
 * Lives apart from `realtime.ts` so the module under test keeps no import of the
 * PocketBase SDK: the client is pure, and this file is the wiring that gives it a
 * transport and a health probe.
 */

import PocketBase from 'pocketbase';
import { checkPocketBaseHealth, getPocketBaseUrl } from '$lib/pocketbase';
import { RealtimeClient, createPocketBaseTransport } from '$lib/realtime';

// The SDK instance lives here, and only here. `pocketbase.ts` does not import it, so
// the browser's data path cannot fall back to a cross-origin SDK call: the one
// direct-to-broker consumer is this live-update stream, which ADR 0006 sanctions.
const pb = new PocketBase(getPocketBaseUrl());

let shared: RealtimeClient | null = null;

export function dashboardRealtime(): RealtimeClient {
	if (!shared) {
		shared = new RealtimeClient({
			transport: createPocketBaseTransport(pb as any),
			isHealthy: () => checkPocketBaseHealth()
		});
	}
	return shared;
}

/** Stop every live subscription. For teardown in tests. */
export function resetDashboardRealtime(): void {
	shared?.reset();
	shared = null;
}
