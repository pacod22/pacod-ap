import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { paraglideVitePlugin } from '@inlang/paraglide-js';
import { defineConfig, loadEnv, type UserConfig } from 'vite';
import { neutralSiteEnv } from './src/lib/server/site-env';
import { csrfTrustedOrigins } from './svelte.config.js';

/**
 * Dev-only PACOD_ shim. `.env` names every SEO/ads key with the `PACOD_` prefix,
 * exactly like pacod-api/.env, but `site-config.ts` reads neutral names. In
 * Docker, compose does the mapping; under `npm run dev` nothing would, so this
 * copies the prefixed set onto the neutral names in process.env, which
 * SvelteKit's `$env/dynamic/private` picks up. Production builds and Vitest are
 * untouched.
 */
function applySiteEnvForDev(mode: string): void {
	const fileEnv = loadEnv(mode, process.cwd(), '');
	const merged = { ...fileEnv, ...process.env };
	Object.assign(process.env, neutralSiteEnv(merged));
}

// `test` is Vitest's key; Vite's UserConfig type does not know it and the two
// packages bundle different Vite type versions, so the object is typed loosely.
const config: UserConfig & { test?: Record<string, unknown> } = {
	plugins: [
		tailwindcss(),
		sveltekit(),
		// Compile-time i18n (English, Bulgarian, German, Russian). Cookie-based
		// locale — no URL prefixes — so no reroute hook is needed. `preferredLanguage`
		// auto-detects the browser language on first visit; the cookie then persists
		// the user's explicit choice from the flag switcher.
		paraglideVitePlugin({
			project: './project.inlang',
			outdir: './src/lib/paraglide',
			// `custom-searchparam` is FIRST on purpose. It resolves `?lang=xx`, which
			// is what gives bg/de/ru crawlable URLs and makes hreflang possible
			// (see src/lib/i18n/lang-param.ts). The server runs custom strategies
			// ahead of the built-ins regardless of position, but the client walks
			// this array in order — putting it first is what keeps the two in
			// agreement and avoids a hydration mismatch.
			//
			// `url` is deliberately absent: enabling it would activate the generated
			// /bg/:path urlPatterns, rewrite every link and require a reroute hook.
			strategy: ['custom-searchparam', 'cookie', 'preferredLanguage', 'baseLocale']
		})
	],

	server: {
		port: 5173,
		strictPort: true,
		// Under `vite dev` only: /api goes straight to the Python backend. This
		// proxy runs BEFORE SvelteKit's hooks, so these calls carry no session
		// Bearer (handleApiProxy never sees them). Signed-in work needs the
		// supported local setup, the compose stack on :8080, where Caddy sends
		// /api/* to SvelteKit and handleApiProxy adds the Bearer.
		proxy: {
			'/api': {
				target: process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000',
				changeOrigin: true
			}
		}
	},

	// The CSRF trusted origins SvelteKit was built with (svelte.config.js), so the
	// server can tell at startup whether pacod-id's back-channel logout will get
	// through (lib/server/identity.ts) — SvelteKit offers no way to read them.
	define: {
		__CSRF_TRUSTED_ORIGINS__: JSON.stringify(csrfTrustedOrigins)
	},

	test: {
		include: ['src/**/*.{test,spec}.{js,ts}']
	},

	build: {
		// Sourcemaps in production for error tracking; remove if you ship
		// source publicly and that's a concern.
		sourcemap: true
	}
};

export default defineConfig(({ command, mode }) => {
	if (command === 'serve' && !process.env.VITEST) applySiteEnvForDev(mode);
	return config;
});
