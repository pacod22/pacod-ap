<script lang="ts">
	// Minimal footer for the pages that don't carry the landing page's big one:
	// the signed-in AppShell (a slim bar pinned under the content).
	import { m } from '$lib/paraglide/messages';
	import { getLocale } from '$lib/paraglide/runtime';
	import { companyLine } from '$lib/legal/company';
	import { cookieConsent } from '$lib/stores/cookie-consent.svelte';

	const year = String(new Date().getFullYear());
	const imprint = $derived(companyLine(getLocale()));
</script>

<footer
	class="flex h-8 shrink-0 items-center gap-4 overflow-x-auto border-t px-4 font-mono text-[11px] whitespace-nowrap sm:px-6"
	style="border-color: var(--color-border); background: var(--color-surface); color: var(--color-text-faint);"
>
	<span>{m.footer_rights({ year, brand: 'PACOD' })}</span>
	<nav class="app-links flex items-center gap-3" aria-label={m.landing_footer_col3()}>
		<a href="/about">{m.landing_footer_about()}</a>
		<a href="/contact">{m.landing_footer_contact()}</a>
		<a href="/privacy">{m.landing_footer_privacy()}</a>
		<a href="/terms">{m.landing_footer_terms()}</a>
		<a href="/cookies">{m.landing_footer_cookies()}</a>
		<button type="button" onclick={() => cookieConsent.open()}>
			{m.footer_cookie_settings()}
		</button>
	</nav>
	<span class="ml-auto hidden truncate md:inline" title={imprint}>{imprint}</span>
</footer>

<style>
	.app-links a,
	.app-links button {
		color: inherit;
		background: none;
		border: 0;
		padding: 0;
		font: inherit;
		cursor: pointer;
		transition: color 0.15s;
	}
	.app-links a:hover,
	.app-links button:hover {
		color: var(--color-brand);
	}
</style>
