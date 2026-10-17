import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import { AssistantStateSchema } from '$lib/features/agent-editor/queries';

// Global LLM configuration of the agent editor's AI assistant — superuser only,
// like the rest of the editor (backend: require_superuser).
export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'superuser')) {
		error(403, 'Superuser role required for the agent editor.');
	}
	const res = await locals.apiFetch('/api/v1/agent-editor/assistant-config');
	if (!res.ok) error(res.status, `Failed to load the assistant settings: ${res.statusText}`);
	return { initial: AssistantStateSchema.parse(await res.json()) };
};
