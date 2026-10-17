import { error } from '@sveltejs/kit';
import type { ZodSchema } from 'zod';
import { hasRole } from '$lib/types/auth';

/**
 * The SSR load every admin list page repeated verbatim: 403 unless admin, fetch
 * the first page from the admin API, validate it, hand it to the page as
 * `initial`. The role check is the real gate (the hooks only redirect
 * unauthenticated users); the UI just hides what it cannot use.
 */
export function adminListLoad<T>(path: string, schema: ZodSchema<T>, what: string) {
	return async ({ locals }: { locals: App.Locals }) => {
		if (!hasRole(locals.user, 'admin')) {
			error(403, `Admin role required to view ${what}.`);
		}
		const res = await locals.apiFetch(path);
		if (!res.ok) {
			error(res.status, `Failed to load ${what}: ${res.statusText}`);
		}
		return { initial: schema.parse(await res.json()) };
	};
}
