import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from '$lib/server/pythonRunner';

/**
 * The version badge the navbar showed before this resolver existed, kept as the
 * last resort so an unconfigured install renders exactly what it always did.
 */
export const DEFAULT_APP_VERSION = 'v0.1';

/** A trimmed, non-empty env var wins; a blank one is treated as unset. */
function readEnvVersion(name: string): string | null {
	const override = process.env[name];
	if (override && override.trim()) {
		return override.trim();
	}
	return null;
}

/**
 * Read the static `version.json` manifest at the project root. CI generates this
 * file (#423), so an absent or unreadable manifest is normal — never throw, and
 * simply let the caller fall through to the next tier. Extra fields are ignored;
 * only `version` matters.
 */
function readManifestVersion(): string | null {
	try {
		const manifestPath = path.join(getProjectRoot(), 'version.json');
		if (!fs.existsSync(manifestPath)) {
			return null;
		}
		const parsed: unknown = JSON.parse(fs.readFileSync(manifestPath, 'utf-8'));
		if (parsed && typeof parsed === 'object') {
			const version = (parsed as { version?: unknown }).version;
			if (typeof version === 'string' && version.trim()) {
				return version.trim();
			}
		}
	} catch {
		// Missing/malformed manifest: fall through rather than breaking the page.
	}
	return null;
}

/**
 * Resolve the display version for the navbar. Precedence, highest first:
 * `APP_VERSION` → `PUBLIC_APP_VERSION` → the static `version.json` manifest →
 * {@link DEFAULT_APP_VERSION}.
 */
export function resolveAppVersion(): string {
	return (
		readEnvVersion('APP_VERSION') ??
		readEnvVersion('PUBLIC_APP_VERSION') ??
		readManifestVersion() ??
		DEFAULT_APP_VERSION
	);
}