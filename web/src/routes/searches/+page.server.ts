import type { PageServerLoad } from './$types';
import type { SavedSearch } from '$lib/types';
import fs from 'fs';
import path from 'path';

function loadFallbackSearches(): SavedSearch[] {
	const candidates = [
		path.resolve(process.cwd(), 'config/searches.yaml'),
		path.resolve(process.cwd(), '../config/searches.yaml')
	];
	for (const p of candidates) {
		if (fs.existsSync(p)) {
			try {
				const content = fs.readFileSync(p, 'utf-8');
				const searches: SavedSearch[] = [];
				const blocks = content.split(/\n\s{2}([a-zA-Z0-9_]+):\n/);
				for (let i = 1; i < blocks.length; i += 2) {
					const id = blocks[i].trim();
					const block = blocks[i + 1];
					const nameMatch = block.match(/name:\s*"([^"]+)"/);
					const descMatch = block.match(/description:\s*"([^"]+)"/);
					const kwMatch = block.match(/keyword:\s*"([^"]+)"/);
					const eduMatch = block.match(/education:\s*"([^"]+)"/);
					const salaryMatch = block.match(/salary:\s*"([^"]+)"/);
					const expMatch = block.match(/experience:\s*"([^"]+)"/);
					const actMatch = block.match(/activity:\s*"([^"]+)"/);

					const scales: string[] = [];
					const scaleMatches = block.matchAll(/-\s*"([^"]+人[^"]*)"/g);
					for (const sm of scaleMatches) {
						scales.push(sm[1]);
					}

					const industries: string[] = [];
					const indSection = block.split(/industries:\s*\n/)[1];
					if (indSection) {
						const indMatches = indSection.matchAll(/-\s*"([^"]+)"/g);
						for (const im of indMatches) {
							industries.push(im[1]);
						}
					}

					searches.push({
						id,
						name: nameMatch ? nameMatch[1] : id,
						description: descMatch ? descMatch[1] : '',
						keyword: kwMatch ? kwMatch[1] : '',
						filter: {
							education: eduMatch ? eduMatch[1] : undefined,
							salary: salaryMatch ? salaryMatch[1] : undefined,
							experience: expMatch ? expMatch[1] : undefined,
							activity: actMatch ? actMatch[1] : undefined,
							company_scales: scales,
							industries: industries
						},
						cron_expression: '',
						is_enabled: false,
						target_task_type: 'AUTO_APPLY'
					});
				}
				if (searches.length > 0) {
					return searches;
				}
			} catch (e) {
				console.warn('Failed to parse fallback searches.yaml:', e);
			}
		}
	}
	return [];
}

export const load: PageServerLoad = async ({ parent, fetch }) => {
	const { pocketbaseUrl } = await parent();
	let searches: SavedSearch[] = [];

	try {
		const res = await fetch(
			`${pocketbaseUrl}/api/collections/saved_searches/records?perPage=200&sort=-created`
		);
		if (res.ok) {
			const data = await res.json();
			if (data.items && data.items.length > 0) {
				searches = data.items.map((r: any) => ({
					id: r.id,
					name: r.name || r.id,
					description: r.description || '',
					keyword: r.keyword || '',
					filter: r.filter || {},
					cron_expression: r.cron_expression || '',
					is_enabled: !!r.is_enabled,
					last_run_at: r.last_run_at,
					target_task_type: r.target_task_type || 'AUTO_APPLY',
					created: r.created,
					updated: r.updated
				}));
			}
		}
	} catch (e) {
		console.warn('Server fetch saved_searches failed:', e);
	}

	if (searches.length === 0) {
		searches = loadFallbackSearches();
	}

	return {
		searches
	};
};
