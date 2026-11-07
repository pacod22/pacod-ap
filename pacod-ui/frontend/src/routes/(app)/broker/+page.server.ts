import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import { BrokerConfigSchema, BrokerStatusSchema } from '$lib/features/broker/queries';

export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to view the broker.');
	}

	const [res, configRes] = await Promise.all([
		locals.apiFetch('/api/v1/broker'),
		locals.apiFetch('/api/v1/broker/config')
	]);
	if (!res.ok) {
		error(res.status, `Failed to load the broker: ${res.statusText}`);
	}
	if (!configRes.ok) {
		error(configRes.status, `Failed to load the broker connection: ${configRes.statusText}`);
	}
	const initial = BrokerStatusSchema.parse(await res.json());
	const config = BrokerConfigSchema.parse(await configRes.json());

	// Changing the connection is gated server-side on the superuser role (it decides where
	// pacod-ap's broker token goes); admins see it read-only and may test it.
	const canEdit = hasRole(locals.user, 'superuser');

	return { initial, config, canEdit };
};
