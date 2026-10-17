/**
 * Display names of the LLM roles that pick a template (Paraglide), and of one
 * template usage ("taxes · Planner", "global · Editor assistant").
 */
import { m } from '$lib/paraglide/messages';
import type { TemplateUsage } from '$lib/features/llm-config/templates';

const ROLE_LABELS: Record<string, () => string> = {
	synthesizer: m.llmtpl_role_synthesizer,
	verifier: m.llmtpl_role_verifier,
	planner: m.llmtpl_role_planner,
	classifier: m.llmtpl_role_classifier,
	suggestions: m.llmtpl_role_suggestions,
	history_summary: m.llmtpl_role_history_summary,
	newsletter: m.llmtpl_role_newsletter,
	triage: m.llmtpl_role_triage,
	analysis_a: m.llmtpl_role_analysis_a,
	analysis_b: m.llmtpl_role_analysis_b,
	judge: m.llmtpl_role_judge,
	remediation: m.llmtpl_role_remediation,
	assistant: m.llmtpl_role_assistant
};

export function roleLabel(role: string): string {
	return ROLE_LABELS[role]?.() ?? role;
}

export function usageLabel(u: TemplateUsage): string {
	const scope = u.profile_id === '__global__' ? m.llmtpl_global() : u.profile_id;
	return `${scope} · ${roleLabel(u.role)}`;
}
