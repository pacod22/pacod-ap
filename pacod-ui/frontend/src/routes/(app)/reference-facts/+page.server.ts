import type { PageServerLoad } from './$types';
import { adminListLoad } from '$lib/server/admin-load';
import { ReferenceFactListSchema } from '$lib/features/reference-facts/queries';

export const load: PageServerLoad = adminListLoad(
	'/api/v1/reference-facts?limit=200&offset=0',
	ReferenceFactListSchema,
	'reference facts'
);
