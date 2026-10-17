import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import { AgentDetailSchema, RegistrySchema } from '$lib/features/agent-editor/queries';

export const load: PageServerLoad = async ({ locals, params }) => {
	if (!hasRole(locals.user, 'superuser')) {
		error(403, 'Superuser role required for the agent editor.');
	}
	const id = encodeURIComponent(params.id);
	const [agentRes, registryRes] = await Promise.all([
		locals.apiFetch(`/api/v1/agent-editor/agents/${id}`),
		locals.apiFetch('/api/v1/agent-editor/registry')
	]);
	if (!agentRes.ok) error(agentRes.status, `Failed to load the agent: ${agentRes.statusText}`);
	if (!registryRes.ok)
		error(registryRes.status, `Failed to load components: ${registryRes.statusText}`);
	return {
		agent: AgentDetailSchema.parse(await agentRes.json()),
		registry: RegistrySchema.parse(await registryRes.json())
	};
};
