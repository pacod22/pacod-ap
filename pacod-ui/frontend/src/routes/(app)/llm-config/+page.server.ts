import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import { LLMConfigSchema } from '$lib/features/llm-config/queries';

export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to view LLM configuration.');
	}

	const res = await locals.apiFetch('/api/v1/llm-config');
	if (!res.ok) {
		error(res.status, `Failed to load LLM configuration: ${res.statusText}`);
	}
	const initial = LLMConfigSchema.parse(await res.json());

	// The agent editor's AI-assistant LLM is superuser-only (as is the editor):
	// regular admins never see — or fetch — that section.
	return { initial, canManageAssistant: hasRole(locals.user, 'superuser') };
};
