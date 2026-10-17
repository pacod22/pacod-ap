import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/**
 * Origins allowed to make cross-site form posts: pacod-id's, for its back-channel
 * logout (a server-to-server `POST /auth/backchannel-logout` that carries the
 * issuer as its Origin; `@pacod/id-sveltekit` documents this).
 *
 * Read at BUILD time — SvelteKit bakes the list into the server and checks it
 * before any hook runs — from `ID_ISSUER`, the same variable the server reads at
 * run time (the Dockerfile takes it as a build arg). The server warns at startup
 * when the issuer it runs with is not in this list (lib/server/identity.ts).
 *
 * @param {string | undefined} issuer
 * @returns {string[]}
 */
function trustedOriginsFor(issuer) {
	if (!issuer) return [];
	try {
		return [new URL(issuer).origin];
	} catch {
		throw new Error(`ID_ISSUER is not a URL: ${issuer}`);
	}
}

export const csrfTrustedOrigins = trustedOriginsFor(process.env.ID_ISSUER);

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),

	kit: {
		// Node adapter: the server signs in through pacod-id as a BFF (session
		// cookie, OIDC callback, back-channel logout — lib/server/identity.ts).
		adapter: adapter({
			out: 'build',
			precompress: false
		}),

		alias: {
			$lib: 'src/lib',
			'$lib/*': 'src/lib/*'
		},

		// CSRF protection is built into SvelteKit form actions. Keep it on:
		// `checkOrigin` is deprecated in favour of `trustedOrigins`; the request's
		// own origin is always trusted, plus pacod-id's (see `csrfTrustedOrigins`).
		csrf: {
			trustedOrigins: csrfTrustedOrigins
		},

		// NOTE: this build-time CSP is currently INERT. SvelteKit turns it into a
		// <meta http-equiv> only when PRERENDERING, and no route in this app sets
		// `export const prerender = true`; on every other response SvelteKit sets a
		// `content-security-policy` header that `handleSecurityHeaders` in
		// hooks.server.ts then overwrites with `headers.set(...)`.
		//
		// The live policy — including the advertising/analytics hosts, which are
		// appended only for the integrations a deployment configures — is built in
		// `lib/server/csp.ts` and applied there. Change that one, not this one.
		// This stays as a safe floor in case a route is ever prerendered.
		csp: {
			mode: 'auto',
			directives: {
				'script-src': ['self'],
				'style-src': ['self', 'unsafe-inline']
			}
		}
	}
};

export default config;
