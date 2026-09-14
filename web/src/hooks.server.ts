import type { Handle, HandleServerError } from '@sveltejs/kit';

/**
 * Format status code with ANSI color for terminal output.
 */
function formatStatusColor(status: number): string {
	const reset = '\x1b[0m';
	if (status >= 500) {
		return `\x1b[31m${status}${reset}`; // Red
	}
	if (status >= 400) {
		return `\x1b[33m${status}${reset}`; // Yellow
	}
	if (status >= 300) {
		return `\x1b[36m${status}${reset}`; // Cyan
	}
	return `\x1b[32m${status}${reset}`; // Green
}

export const handle: Handle = async ({ event, resolve }) => {
	const start = performance.now();
	const { method } = event.request;
	const path = event.url.pathname;
	const search = event.url.search;

	const response = await resolve(event);

	if (path.startsWith('/api')) {
		const duration = Math.round(performance.now() - start);
		const status = response.status;
		const coloredStatus = formatStatusColor(status);
		const timestamp = new Date().toISOString().replace('T', ' ').substring(0, 19);

		console.log(
			`[${timestamp}] [API] ${method.padEnd(6)} ${coloredStatus} ${path}${search} (${duration}ms)`
		);
	}

	return response;
};

export const handleError: HandleServerError = ({ error, event, status, message }) => {
	const timestamp = new Date().toISOString().replace('T', ' ').substring(0, 19);
	console.error(`[${timestamp}] [SERVER ERROR] ${event.request.method} ${event.url.pathname}:`, error);
	return {
		message: message || 'Internal Server Error'
	};
};
