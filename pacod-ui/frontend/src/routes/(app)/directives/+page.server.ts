import type { PageServerLoad } from './$types';
import { adminListLoad } from '$lib/server/admin-load';
import { DirectiveListSchema } from '$lib/features/directives/queries';

export const load: PageServerLoad = adminListLoad(
	'/api/v1/directives?limit=200&offset=0',
	DirectiveListSchema,
	'directives'
);
