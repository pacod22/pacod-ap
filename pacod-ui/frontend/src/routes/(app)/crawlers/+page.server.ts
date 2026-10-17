import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { hasRole } from '$lib/types/auth';
import { CrawlerVisitListSchema } from '$lib/features/crawlers/queries';

/**
 * Crawler traffic is operational data about the site's public surface, so it is
 * admin-only like the rest of the observability group.
 */
export const load: PageServerLoad = async ({ locals }) => {
	if (!hasRole(locals.user, 'admin')) {
		error(403, 'Admin role required to view crawler traffic.');
	}

	// The list is re-fetched client-side with filters, so this load is not what
	// populates the table. It earns its keep by exercising the SERVER-side path:
	// `lib/server/api.ts` keeps its own copy of ADMIN_PATH_PREFIXES, and if
	// `crawler-visits` were missing from that copy only SSR would 404 — the
	// browser path would keep working and hide it. Failing here makes that
	// mistake immediate instead of silent.
	const res = await locals.apiFetch('/api/v1/crawler-visits?limit=50&offset=0');
	if (!res.ok) {
		error(res.status, `Failed to load crawler visits: ${res.statusText}`);
	}
	const initial = CrawlerVisitListSchema.parse(await res.json());
	return { initial };
};
