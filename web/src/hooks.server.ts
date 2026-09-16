import type { Handle, HandleServerError } from '@sveltejs/kit';

const ANSI_RESET = '\x1b[0m';
const ANSI_RED = '\x1b[31m';
const ANSI_YELLOW = '\x1b[33m';
const ANSI_CYAN = '\x1b[36m';
const ANSI_GREEN = '\x1b[32m';

/**
 * Format current timestamp as YYYY-MM-DD HH:mm:ss
 */
function formatTimestamp(date = new Date()): string {
	return date.toISOString().replace('T', ' ').substring(0, 19);
}

/**
 * Format status code with ANSI color for terminal output.
 */
function formatStatusColor(status: number): string {
	if (status >= 500) {
		return `${ANSI_RED}${status}${ANSI_RESET}`; // Red (5xx)
	}
	if (status >= 400) {
		return `${ANSI_YELLOW}${status}${ANSI_RESET}`; // Yellow (4xx)
	}
	if (status >= 300) {
		return `${ANSI_CYAN}${status}${ANSI_RESET}`; // Cyan (3xx)
	}
	if (status >= 200) {
		return `${ANSI_GREEN}${status}${ANSI_RESET}`; // Green (2xx)
	}
	return `${status}`; // Default / 1xx
}

export const handle: Handle = async ({ event, resolve }) => {
	const path = event.url.pathname;

	// Graceful fallback for legacy favicon requests without binary bloat
	if (path === '/favicon.png' || path === '/favicon.ico') {
		return new Response(null, {
			status: 302,
			headers: { Location: '/favicon.svg' }
		});
	}

	const isApiRoute = path === '/api' || path.startsWith('/api/');

	if (!isApiRoute) {
		return resolve(event);
	}

	const start = performance.now();
	const { method } = event.request;
	const search = event.url.search;

	try {
		const response = await resolve(event);
		const duration = Math.round(performance.now() - start);
		const coloredStatus = formatStatusColor(response.status);
		const timestamp = formatTimestamp();

		console.log(
			`[${timestamp}] [API] ${method.padEnd(6)} ${coloredStatus} ${path}${search} (${duration}ms)`
		);
		return response;
	} catch (error) {
		const duration = Math.round(performance.now() - start);
		const coloredStatus = formatStatusColor(500);
		const timestamp = formatTimestamp();

		console.log(
			`[${timestamp}] [API] ${method.padEnd(6)} ${coloredStatus} ${path}${search} (${duration}ms)`
		);
		throw error;
	}
};

export const handleError: HandleServerError = ({ error, event, status, message }) => {
	const timestamp = formatTimestamp();
	const fullPath = `${event.url.pathname}${event.url.search}`;

	// Only log 5xx or unhandled server exceptions as [SERVER ERROR]
	// 404 and other client errors (< 500) are standard HTTP responses, not server failures
	if ((status ?? 500) >= 500) {
		console.error(`[${timestamp}] [SERVER ERROR] ${event.request.method} ${fullPath}:`, error);
	}

	return {
		message: message || (status === 404 ? 'Not Found' : 'Internal Server Error')
	};
};
