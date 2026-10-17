import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import { LlmModelsResponseSchema } from '$lib/features/llm-models/queries';

export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to view LLM models.');
	}

	const res = await locals.apiFetch('/api/v1/llm-models');
	if (!res.ok) {
		error(res.status, `Failed to load LLM models: ${res.statusText}`);
	}
	const initial = LlmModelsResponseSchema.parse(await res.json());

	return { initial };
};
