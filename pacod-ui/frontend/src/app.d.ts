// See https://svelte.dev/docs/kit/types#app
// for information about these interfaces.

import type { IdSession } from '@pacod/id-sveltekit';
import type { User } from '$lib/types/auth';

declare global {
	/**
	 * The CSRF trusted origins SvelteKit was built with (svelte.config.js, injected by
	 * vite.config.ts), so the server can tell whether pacod-id's back-channel logout gets in.
	 */
	const __CSRF_TRUSTED_ORIGINS__: string[];

	namespace App {
		interface Error {
			code?: string;
			message: string;
		}

		interface Locals {
			/**
			 * The pacod-id session of this request (`@pacod/id-sveltekit`), its access token
			 * refreshed when due; `null` when signed out. Server-only: it holds the tokens.
			 */
			session: IdSession | null;
			/** Who the session is, from `/api/v1/auth/me` (cached 30 s per session). */
			user: User | null;
			/**
			 * The API refused this request's session (401 on `/auth/me`), which was then
			 * forgotten: the next sign-in asks for the password (`prompt=login`), or a
			 * silent sign-in at pacod-id would bring the refused account straight back.
			 */
			sessionEnded: boolean;
			/** Short random ID assigned per-request for log correlation. */
			rid: string;
			/** Fetch helper bound to the Python backend with auth headers attached */
			apiFetch: (input: string, init?: RequestInit) => Promise<Response>;
		}

		interface PageData {
			user?: User | null;
		}

		// eslint-disable-next-line @typescript-eslint/no-empty-object-type -- SvelteKit placeholder
		interface PageState {}

		// eslint-disable-next-line @typescript-eslint/no-empty-object-type -- SvelteKit placeholder
		interface Platform {}
	}
}

export {};
