import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import { AgentListSchema, RegistrySchema } from '$lib/features/agent-editor/queries';

// The visual agent editor is SUPERUSER-only (backend: require_superuser on every
// /api/v1/agent-editor route — this gate only keeps the page from rendering).
export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'superuser')) {
		error(403, 'Superuser role required for the agent editor.');
	}
	const [agentsRes, registryRes] = await Promise.all([
		locals.apiFetch('/api/v1/agent-editor/agents'),
		locals.apiFetch('/api/v1/agent-editor/registry')
	]);
	if (!agentsRes.ok) error(agentsRes.status, `Failed to load agents: ${agentsRes.statusText}`);
	if (!registryRes.ok)
		error(registryRes.status, `Failed to load components: ${registryRes.statusText}`);
	return {
		agents: AgentListSchema.parse(await agentsRes.json()),
		registry: RegistrySchema.parse(await registryRes.json())
	};
};
