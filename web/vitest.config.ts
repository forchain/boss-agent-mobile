import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

// The browser condition lets component tests (jsdom) mount the client build of Svelte;
// the node-environment route tests are unaffected by it.
export default defineConfig({
	plugins: [sveltekit()],
	resolve: { conditions: ['browser'] },
	test: {
		include: ['src/**/*.{test,spec}.{js,ts}'],
		testTimeout: 60000
	}
});
