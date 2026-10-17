/**
 * Links into pacod-id's sign-in, for pages and server code alike.
 *
 * `/auth/login` and `/auth/logout` are served by `@pacod/id-sveltekit`'s handle
 * (lib/server/identity.ts), not by SvelteKit routes: a link to one needs
 * `data-sveltekit-reload`, or the client-side router tries to render it as a
 * page. Sign-out is a POST (`<form method="POST" action={SIGN_OUT_PATH}>`).
 */

export const SIGN_IN_PATH = '/auth/login';
export const SIGN_OUT_PATH = '/auth/logout';

/** Where a visitor lands after signing in when nothing else was asked for. */
export const DEFAULT_LANDING = '/chat';

export interface SignInOptions {
	/** Open pacod-id's sign-up instead of its sign-in (`prompt=create`). */
	create?: boolean;
	/**
	 * The API has just refused this browser's session: ask for the password again
	 * (`prompt=login`) instead of signing in silently, so a refusal cannot loop.
	 */
	reauth?: boolean;
	/** The UI language, passed on as `ui_locales` so pacod-id answers in it. */
	locale?: string;
}

/**
 * The sign-in address that comes back to `next`, a path of this application
 * (anything else is replaced with `/` by the handle).
 */
export function signInHref(next?: string | null, opts: SignInOptions = {}): string {
	const q = new URLSearchParams({ next: next || DEFAULT_LANDING });
	if (opts.create) q.set('prompt', 'create');
	else if (opts.reauth) q.set('prompt', 'login');
	if (opts.locale) q.set('ui_locales', opts.locale);
	return `${SIGN_IN_PATH}?${q}`;
}
