import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import { PromptListSchema } from '$lib/features/agent-prompts/queries';

export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to view agent prompts.');
	}

	const res = await locals.apiFetch('/api/v1/agent-prompts');
	if (!res.ok) {
		error(res.status, `Failed to load agent prompts: ${res.statusText}`);
	}
	const initial = PromptListSchema.parse(await res.json());

	// Editing is gated server-side on the superuser role; the UI hides the
	// controls for regular admins (read-only).
	const canEdit = hasRole(locals.user, 'superuser');

	return { initial, canEdit };
};
