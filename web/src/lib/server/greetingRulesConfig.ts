import fs from 'node:fs';
import path from 'node:path';
import yaml from 'js-yaml';
import { getProjectRoot } from './pythonRunner';
import type { GreetingStyleRule } from '$lib/types';

export function getGreetingRulesConfigPath(): string {
	const root = getProjectRoot();
	return path.join(root, 'config', 'greeting_rules.local.yaml');
}

export function parseGreetingRulesYaml(content: string): GreetingStyleRule[] {
	let data: unknown;
	try {
		data = yaml.load(content);
	} catch (e) {
		console.warn('[GreetingRulesConfig] YAML parse error:', e);
		return [];
	}

	let rawRules: unknown[] = [];
	if (data && typeof data === 'object' && 'rules' in data && Array.isArray((data as any).rules)) {
		rawRules = (data as any).rules;
	} else if (Array.isArray(data)) {
		rawRules = data;
	} else {
		return [];
	}

	return rawRules
		.filter((r): r is Record<string, unknown> => r !== null && typeof r === 'object')
		.filter((r) => r.condition && r.instruction)
		.map((r) => ({
			id: String(r.id || `rule_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`),
			condition: String(r.condition),
			instruction: String(r.instruction),
			enabled: r.enabled !== false,
			source_job: String(r.source_job || ''),
			created_at: String(r.created_at || new Date().toISOString())
		}));
}

export function serializeGreetingRulesYaml(rules: GreetingStyleRule[]): string {
	const header = [
		'# ============================================================================== #',
		'# Boss Agent Mobile - Greeting Style Rules & Long-term Preferences',
		'# Auto-generated & updated by Boss Agent Mobile Web UI or manual editing',
		'# ============================================================================== #',
		''
	].join('\n');

	const data = {
		rules: rules.map((r) => ({
			id: r.id,
			condition: r.condition,
			instruction: r.instruction,
			enabled: r.enabled,
			...(r.source_job ? { source_job: r.source_job } : {}),
			created_at: r.created_at || new Date().toISOString()
		}))
	};

	return header + '\n' + yaml.dump(data, { lineWidth: -1, noRefs: true, sortKeys: false });
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
