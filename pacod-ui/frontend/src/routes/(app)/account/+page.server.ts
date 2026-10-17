import { fail } from '@sveltejs/kit';
import type { Actions, PageServerLoad } from './$types';
import { logger } from '$lib/server/logger';
import { identity } from '$lib/server/identity';

export interface NewsletterSubscriptionInfo {
	profile_id: string;
	name: string;
	subscribed: boolean;
}

export const load: PageServerLoad = async ({ locals }) => {
	// Newsletter subscriptions: one row per profile the user is assigned to.
	let newsletters: NewsletterSubscriptionInfo[] = [];
	try {
		const res = await locals.apiFetch('/api/v1/my-profiles');
		if (res.ok) {
			const profiles = (await res.json()) as Array<{ id: string; name: string }>;
			newsletters = await Promise.all(
				profiles.map(async (p) => {
					let subscribed = false;
					try {
						const sub = await locals.apiFetch(
							`/api/v1/newsletter/subscription?profile_id=${encodeURIComponent(p.id)}`
						);
						if (sub.ok) subscribed = ((await sub.json()) as { subscribed: boolean }).subscribed;
					} catch {
						/* best-effort */
					}
					return { profile_id: p.id, name: p.name, subscribed };
				})
			);
		}
	} catch (err) {
		logger.warn('account.newsletters.unreachable', { rid: locals.rid, error: String(err) });
	}

	// Password, signed-in devices and connected Google accounts live at pacod-id.
	// The issuer is run-time configuration, so it reaches the page through this
	// load rather than a PUBLIC_ variable baked into the build.
	return { newsletters, accountUrl: `${identity().issuer}/account` };
};

export const actions: Actions = {
	toggleNewsletter: async ({ request, locals }) => {
		const data = await request.formData();
		const profile_id = (data.get('profile_id') as string)?.trim();
		const subscribe = data.get('subscribe') === 'true';
		if (!profile_id) return fail(400, { error: 'Missing profile id.' });
		const res = await locals.apiFetch('/api/v1/newsletter/subscription', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ profile_id, subscribe })
		});
		if (!res.ok) {
			logger.warn('account.newsletter.toggle_failed', { rid: locals.rid, status: res.status });
			return fail(res.status, { error: 'Failed to update newsletter subscription.' });
		}
		return { newsletterToggled: true };
	}
};
