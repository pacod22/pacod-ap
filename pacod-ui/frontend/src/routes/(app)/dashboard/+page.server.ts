import type { PageServerLoad } from './$types';

/**
 * The dashboard is available to every authenticated user. Admins see the
 * platform overview + runtime toggles; regular members see a personal
 * overview (usage, activity, recent conversations, subscription). The page
 * component branches on the role from `data.user` (provided by the layout).
 */
export const load: PageServerLoad = async () => {
	return {};
};
