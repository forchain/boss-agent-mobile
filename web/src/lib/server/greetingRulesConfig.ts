import fs from 'node:fs';
import path from 'node:path';
import { getProjectRoot } from './pythonRunner';
import type { GreetingStyleRule } from '$lib/types';

export function getGreetingRulesConfigPath(): string {
	const root = getProjectRoot();
	return path.join(root, 'config', 'greeting_rules.local.yaml');
}

export function parseGreetingRulesYaml(content: string): GreetingStyleRule[] {
	const rules: GreetingStyleRule[] = [];
	const lines = content.split('\n');
	let currentRule: Partial<GreetingStyleRule> | null = null;

	for (const rawLine of lines) {
		const line = rawLine.trim();
		if (!line || line.startsWith('#')) continue;

		if (line.startsWith('- id:') || (line.startsWith('-') && line.includes('condition:'))) {
			if (currentRule && currentRule.condition && currentRule.instruction) {
				rules.push({
					id: currentRule.id || `rule_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
					condition: currentRule.condition,
					instruction: currentRule.instruction,
					enabled: currentRule.enabled !== false,
					source_job: currentRule.source_job || '',
					created_at: currentRule.created_at || new Date().toISOString()
				});
			}
			currentRule = {};
		}

		if (!currentRule) continue;

		if (line.includes('id:')) {
			const val = line.replace(/^[-\s]*id:\s*/, '').replace(/^["']|["']$/g, '').trim();
			currentRule.id = val;
		} else if (line.includes('condition:')) {
			const val = line.replace(/^[-\s]*condition:\s*/, '').replace(/^["']|["']$/g, '').trim();
			currentRule.condition = val;
		} else if (line.includes('instruction:')) {
			const val = line.replace(/^[-\s]*instruction:\s*/, '').replace(/^["']|["']$/g, '').trim();
			currentRule.instruction = val;
		} else if (line.includes('enabled:')) {
			const val = line.replace(/^[-\s]*enabled:\s*/, '').trim().toLowerCase();
			currentRule.enabled = val === 'true';
		} else if (line.includes('source_job:')) {
			const val = line.replace(/^[-\s]*source_job:\s*/, '').replace(/^["']|["']$/g, '').trim();
			currentRule.source_job = val;
		} else if (line.includes('created_at:')) {
			const val = line.replace(/^[-\s]*created_at:\s*/, '').replace(/^["']|["']$/g, '').trim();
			currentRule.created_at = val;
		}
	}

	if (currentRule && currentRule.condition && currentRule.instruction) {
		rules.push({
			id: currentRule.id || `rule_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
			condition: currentRule.condition,
			instruction: currentRule.instruction,
			enabled: currentRule.enabled !== false,
			source_job: currentRule.source_job || '',
			created_at: currentRule.created_at || new Date().toISOString()
		});
	}

	return rules;
}

export function serializeGreetingRulesYaml(rules: GreetingStyleRule[]): string {
	const lines: string[] = [
		'# ============================================================================== #',
		'# Boss Agent Mobile - Greeting Style Rules & Long-term Preferences',
		'# Auto-generated & updated by Boss Agent Mobile Web UI or manual editing',
		'# ============================================================================== #',
		'',
		'rules:'
	];

	for (const r of rules) {
		lines.push(`  - id: ${JSON.stringify(r.id)}`);
		lines.push(`    condition: ${JSON.stringify(r.condition)}`);
		lines.push(`    instruction: ${JSON.stringify(r.instruction)}`);
		lines.push(`    enabled: ${r.enabled ? 'true' : 'false'}`);
		if (r.source_job) {
			lines.push(`    source_job: ${JSON.stringify(r.source_job)}`);
		}
		lines.push(`    created_at: ${JSON.stringify(r.created_at || new Date().toISOString())}`);
	}

	lines.push('');
	return lines.join('\n');
}

export function readGreetingRules(): GreetingStyleRule[] {
	const root = getProjectRoot();
	const localPath = path.join(root, 'config', 'greeting_rules.local.yaml');
	const examplePath = path.join(root, 'config', 'greeting_rules.example.yaml');

	if (fs.existsSync(localPath)) {
		try {
			const content = fs.readFileSync(localPath, 'utf-8');
			const parsed = parseGreetingRulesYaml(content);
			if (parsed.length > 0) return parsed;
		} catch (e) {
			console.warn('[GreetingRulesConfig] Failed to parse greeting_rules.local.yaml:', e);
		}
	}

	if (fs.existsSync(examplePath)) {
		try {
			const content = fs.readFileSync(examplePath, 'utf-8');
			return parseGreetingRulesYaml(content);
		} catch (e) {
			console.warn('[GreetingRulesConfig] Failed to parse greeting_rules.example.yaml:', e);
		}
	}

	return [];
}

export function writeGreetingRules(rules: GreetingStyleRule[]): void {
	const root = getProjectRoot();
	const localPath = path.join(root, 'config', 'greeting_rules.local.yaml');
	fs.mkdirSync(path.dirname(localPath), { recursive: true });
	const yamlContent = serializeGreetingRulesYaml(rules);
	fs.writeFileSync(localPath, yamlContent, 'utf-8');
}
