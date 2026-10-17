import { redirect } from '@sveltejs/kit';
import type { LayoutServerLoad } from './$types';
import { signInHref } from '$lib/auth-links';
import { getLocale } from '$lib/paraglide/runtime';
import { MyProfileListSchema, type MyProfile } from '$lib/features/profiles/queries';

/**
 * Belt-and-suspenders. hooks.server.ts already redirects unauthenticated
 * users away from /(app); this load function asserts the invariant so the
 * page component can safely treat `data.user` as non-null.
 *
 * Also loads the agents (chat profiles) the user may talk to, so the AppShell
 * can render one sidebar entry per agent. SSR here (rather than a browser query)
 * keeps the primary nav from popping in after hydration.
 */
export const load: LayoutServerLoad = async ({ locals, url }) => {
	if (!locals.user) {
		redirect(
			303,
			signInHref(url.pathname + url.search, {
				locale: getLocale(),
				reauth: locals.sessionEnded
			})
		);
	}

	// Non-fatal: a failure here must not break the whole shell — fall back to an
	// empty list, which renders a single generic "Chat" link.
	let myProfiles: MyProfile[] = [];
	try {
		const res = await locals.apiFetch('/api/v1/my-profiles');
		if (res.ok) myProfiles = MyProfileListSchema.parse(await res.json());
	} catch {
		myProfiles = [];
	}

	return { user: locals.user, myProfiles };
};
