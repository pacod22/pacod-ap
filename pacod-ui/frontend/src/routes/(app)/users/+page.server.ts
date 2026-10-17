import type { PageServerLoad } from './$types';
import { adminListLoad } from '$lib/server/admin-load';
import { UserListSchema } from '$lib/features/users/queries';

export const load: PageServerLoad = adminListLoad(
	'/api/v1/users?limit=50&offset=0',
	UserListSchema,
	'users'
);
