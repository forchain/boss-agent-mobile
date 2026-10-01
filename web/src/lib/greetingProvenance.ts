/**
 * Greeting provenance — whose words a Job Record is holding (issue #300).
 *
 * The preview tier left the run modes in #298, so an operator who wants to read a greeting
 * before anything is sent does it here: generate or edit the copy in the dashboard and save
 * it. That only works if the record knows the saved text is the human's, because the agent's
 * next sweep would otherwise draft a new one straight over it.
 *
 * `human` is the only value that changes the agent's behaviour. Anything else — including a
 * record written before this field existed — means "not approved by a person", which keeps
 * the rollout safe: an unknown source costs a regenerated draft, never an unapproved send.
 * The Python side declares the same two values in `boss_agent.models`.
 */

export const GREETING_SOURCE_AGENT = 'agent_draft' as const;
export const GREETING_SOURCE_HUMAN = 'human' as const;

/** The two provenances a writer may claim. Anything else reads as unknown. */
export type GreetingSource = typeof GREETING_SOURCE_AGENT | typeof GREETING_SOURCE_HUMAN;

export type GreetingProvenance = 'none' | 'human' | 'agent' | 'unknown';

interface GreetingBearer {
	greeting_message?: string | null;
	greeting_source?: string | null;
}

/** Who wrote this record's greeting, from its stored text and provenance. */
export function greetingProvenance(record?: GreetingBearer | null): GreetingProvenance {
	if (!record || !record.greeting_message || !record.greeting_message.trim()) return 'none';
	const source = (record.greeting_source ?? '').trim();
	if (source === GREETING_SOURCE_HUMAN) return 'human';
	if (source === GREETING_SOURCE_AGENT) return 'agent';
	return 'unknown';
}

/** The panel label for a provenance, in the operator's own vocabulary. */
export function greetingProvenanceLabel(provenance: GreetingProvenance): string {
	switch (provenance) {
		case 'human':
			return '✍️ 人工稿 · 下次运行原文发送';
		case 'agent':
			return '🤖 Agent 草稿 · 下次运行可重新生成';
		case 'unknown':
			return '❓ 来源未知 · 按 Agent 草稿处理';
		default:
			return '';
	}
}

/** The patch a human-authored save writes: the text and its provenance move together. */
export function humanGreetingPatch(
	text: string
): { greeting_message: string; greeting_source: typeof GREETING_SOURCE_HUMAN } {
	return { greeting_message: text, greeting_source: GREETING_SOURCE_HUMAN };
}
