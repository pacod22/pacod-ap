import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import { NewsletterConfigSchema } from '$lib/features/newsletter/queries';

export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to manage the newsletter.');
	}

	// Default-scoped snapshot; the browser query refetches for the selected profile.
	const res = await locals.apiFetch('/api/v1/newsletter-config');
	if (!res.ok) {
		error(res.status, `Failed to load newsletter config: ${res.statusText}`);
	}
	const initial = NewsletterConfigSchema.parse(await res.json());

	return { initial };
};
