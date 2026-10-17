<script lang="ts">
	// Shared chrome for the public information & legal pages (/about, /contact,
	// /privacy, /terms, /cookies). Deliberately lighter than the landing page:
	// a minimal header (brand → home, theme toggle, language switcher) and the
	// same footer, so these pages read as part of the same site. No account
	// menu / user state and no reveal-on-scroll — none of it earns its keep here.
	import { m } from '$lib/paraglide/messages';
	import { getLocale, setLocale, type Locale } from '$lib/paraglide/runtime';
	import { companyLine } from '$lib/legal/company';
	import LanguageSwitcher from '$lib/components/layout/LanguageSwitcher.svelte';
	import SocialLinks from '$lib/components/social/SocialLinks.svelte';
	import WhatsAppButton from '$lib/components/social/WhatsAppButton.svelte';
	import { page } from '$app/state';
	import { onlyEnabled } from '$lib/i18n/enabled-locales';
	import { theme } from '$lib/stores/theme.svelte';
	import { Sun, Moon } from 'lucide-svelte';

	let { children }: { children: import('svelte').Snippet } = $props();

	const site = $derived(page.data.site);

	// --- Footer language pills (mirror the landing page) ---
	const FOOTER_LANGS: { code: Locale; label: string }[] = [
		{ code: 'bg', label: 'БГ' },
		{ code: 'en', label: 'EN' },
		{ code: 'de', label: 'DE' },
		{ code: 'ru', label: 'RU' }
	];
	let current = $derived(getLocale());
	function pickLang(code: Locale) {
		if (code !== current) setLocale(code);
	}
</script>

<div class="marketing">
	<!-- ============ HEADER ============ -->
	<header class="site-header">
		<div class="header-inner container">
			<a href="/" class="brand" aria-label="PACOD">
				<span class="brand-mark">P</span>
				<span class="brand-name">PACOD</span>
			</a>

			<div class="header-actions">
				<LanguageSwitcher />
				<button
					type="button"
					class="icon-btn"
					onclick={() => theme.toggle()}
					aria-label={theme.current === 'dark'
						? m.appshell_theme_to_light()
						: m.appshell_theme_to_dark()}
				>
					{#if theme.current === 'dark'}
						<Sun size={18} />
					{:else}
						<Moon size={18} />
					{/if}
				</button>
			</div>
		</div>
	</header>

	<!-- ============ PAGE BODY ============ -->
	<main class="page">
		<div class="container">
			{@render children()}
		</div>
	</main>

	<!-- ============ FOOTER ============ -->
	<footer class="site-footer">
		<div class="container">
			<div class="footer-grid">
				<div class="footer-brand">
					<a href="/" class="brand"
						><span class="brand-mark">P</span><span class="brand-name">PACOD</span></a
					>
					<p>{m.landing_footer_tagline()}</p>
					<div class="footer-social">
						<SocialLinks {site} />
						<WhatsAppButton {site} />
					</div>
				</div>
				<div class="footer-col">
					<h5>{m.landing_footer_col1()}</h5>
					<ul>
						<li><a href="/#agents">{m.landing_nav_agents()}</a></li>
						<li><a href="/#bestof">{m.landing_nav_bestof()}</a></li>
						<li><a href="/#newsletter">{m.landing_nav_newsletter()}</a></li>
					</ul>
				</div>
				<div class="footer-col">
					<h5>{m.landing_footer_col2()}</h5>
					<ul>
						<li><a href="/about">{m.landing_footer_about()}</a></li>
						<li><a href="/contact">{m.landing_footer_contact()}</a></li>
					</ul>
				</div>
				<div class="footer-col">
					<h5>{m.landing_footer_col3()}</h5>
					<ul>
						<li><a href="/privacy">{m.landing_footer_privacy()}</a></li>
						<li><a href="/terms">{m.landing_footer_terms()}</a></li>
						<li><a href="/cookies">{m.landing_footer_cookies()}</a></li>
					</ul>
				</div>
			</div>
			<div class="footer-bottom">
				<span class="copy">{m.landing_footer_rights()}</span>
				<div class="footer-langs">
					{#each onlyEnabled(FOOTER_LANGS, page.data.enabledLocales) as loc (loc.code)}
						<button class:active={loc.code === current} onclick={() => pickLang(loc.code)}
							>{loc.label}</button
						>
					{/each}
				</div>
			</div>
			<p class="footer-company">{companyLine(current)}</p>
		</div>
	</footer>
</div>

<style>
	/* Public info/legal pages. Colours use the global --color-* tokens so the
	   pages are theme-aware; layout tokens (radii, fonts) are global too. */
	.marketing {
		min-height: 100dvh;
		display: flex;
		flex-direction: column;
		background: var(--color-bg);
		color: var(--color-text);
	}

	.container {
		width: 100%;
		max-width: 1120px;
		margin-inline: auto;
		padding-inline: 20px;
	}

	/* ---------- Header ---------- */
	.site-header {
		position: sticky;
		top: 0;
		z-index: 50;
		background: color-mix(in oklch, var(--color-surface) 88%, transparent);
		backdrop-filter: saturate(1.4) blur(14px);
		border-bottom: 1px solid var(--color-border);
	}
	.header-inner {
		display: flex;
		align-items: center;
		justify-content: space-between;
		height: 68px;
		gap: 16px;
	}
	.header-actions {
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.brand {
		display: inline-flex;
		align-items: center;
		gap: 10px;
		font-family: var(--font-display);
		font-weight: 800;
		font-size: 1.28rem;
		letter-spacing: -0.03em;
		color: var(--color-text);
	}
	.brand-mark {
		width: 34px;
		height: 34px;
		border-radius: 9px;
		background: linear-gradient(
			150deg,
			var(--color-brand) 0%,
			color-mix(in oklch, var(--color-brand) 70%, #000) 100%
		);
		display: grid;
		place-items: center;
		color: #fff;
		font-family: var(--font-display);
		font-weight: 800;
		font-size: 1rem;
		box-shadow: var(--shadow-sm);
	}

	.icon-btn {
		display: inline-grid;
		place-items: center;
		width: 34px;
		height: 34px;
		border-radius: 10px;
		border: none;
		background: transparent;
		color: var(--color-text-muted);
		cursor: pointer;
		transition:
			background 0.15s,
			color 0.15s;
	}
	.icon-btn:hover {
		background: var(--color-surface-2);
		color: var(--color-text);
	}

	/* ---------- Page body ---------- */
	.page {
		flex: 1;
		padding-block: 56px 72px;
	}

	/* Long-form prose shared by every info/legal page. Children render markup
	   inside .container > (their own .legal wrapper) — see each +page.svelte. */
	:global(.marketing .legal) {
		max-width: 760px;
		margin-inline: auto;
	}
	:global(.marketing .legal .eyebrow) {
		font-family: var(--font-mono);
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.14em;
		color: var(--color-brand);
		font-weight: 600;
		margin: 0 0 10px;
	}
	:global(.marketing .legal h1) {
		font-family: var(--font-display);
		font-size: clamp(2rem, 4vw, 2.75rem);
		font-weight: 800;
		letter-spacing: -0.03em;
		line-height: 1.08;
		margin: 0 0 12px;
	}
	:global(.marketing .legal .lead) {
		font-size: 1.1rem;
		color: var(--color-text-muted);
		line-height: 1.6;
		margin: 0 0 8px;
	}
	:global(.marketing .legal .meta) {
		font-family: var(--font-mono);
		font-size: 0.78rem;
		color: var(--color-text-faint);
		margin: 0 0 40px;
	}
	:global(.marketing .legal h2) {
		font-family: var(--font-display);
		font-size: 1.4rem;
		font-weight: 700;
		letter-spacing: -0.02em;
		margin: 40px 0 14px;
		padding-top: 8px;
	}
	:global(.marketing .legal h3) {
		font-size: 1.05rem;
		font-weight: 700;
		margin: 24px 0 10px;
	}
	:global(.marketing .legal p),
	:global(.marketing .legal li) {
		font-size: 1rem;
		line-height: 1.7;
		color: var(--color-text);
	}
	:global(.marketing .legal p) {
		margin: 0 0 16px;
	}
	:global(.marketing .legal ul),
	:global(.marketing .legal ol) {
		margin: 0 0 16px;
		padding-left: 1.35rem;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	:global(.marketing .legal a) {
		color: var(--color-brand);
		text-decoration: underline;
		text-underline-offset: 2px;
	}
	:global(.marketing .legal strong) {
		font-weight: 700;
	}
	:global(.marketing .legal .placeholder) {
		color: var(--color-text-muted);
		font-style: italic;
	}
	/* Callout box for "this is a template" notices */
	:global(.marketing .legal .note) {
		background: var(--color-surface-2);
		border: 1px solid var(--color-border);
		border-radius: var(--r-md, 12px);
		padding: 16px 18px;
		margin: 0 0 32px;
		font-size: 0.92rem;
		color: var(--color-text-muted);
		line-height: 1.6;
	}
	/* Concrete data tables (cookies, contact details) */
	:global(.marketing .legal .table-wrap) {
		overflow-x: auto;
		margin: 0 0 24px;
		border: 1px solid var(--color-border);
		border-radius: var(--r-md, 12px);
	}
	:global(.marketing .legal table) {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.9rem;
	}
	:global(.marketing .legal th),
	:global(.marketing .legal td) {
		text-align: left;
		padding: 11px 14px;
		border-bottom: 1px solid var(--color-border);
		vertical-align: top;
	}
	:global(.marketing .legal th) {
		font-family: var(--font-mono);
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--color-text-faint);
		font-weight: 600;
		background: var(--color-surface-2);
	}
	:global(.marketing .legal tr:last-child td) {
		border-bottom: none;
	}
	:global(.marketing .legal td code) {
		font-family: var(--font-mono);
		font-size: 0.85em;
		background: var(--color-surface-2);
		padding: 1px 6px;
		border-radius: 5px;
	}

	/* ---------- Footer (ported from the landing page) ---------- */
	.site-footer {
		background: var(--color-surface-2);
		border-top: 1px solid var(--color-border);
		padding-block: 56px 32px;
	}
	.footer-grid {
		display: grid;
		grid-template-columns: 1fr;
		gap: 36px;
	}
	@media (min-width: 720px) {
		.footer-grid {
			grid-template-columns: 1.6fr 1fr 1fr 1fr;
			gap: 32px;
		}
	}
	.footer-social {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem 1rem;
		margin-top: 1rem;
	}

	.footer-brand .brand {
		margin-bottom: 14px;
	}
	.footer-brand p {
		font-size: 0.9rem;
		color: var(--color-text-muted);
		max-width: 30ch;
	}
	.footer-col h5 {
		font-family: var(--font-mono);
		font-size: 0.68rem;
		text-transform: uppercase;
		letter-spacing: 0.12em;
		color: var(--color-text-faint);
		margin: 0 0 14px;
		font-weight: 600;
	}
	.footer-col ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.footer-col a {
		color: var(--color-text-muted);
		font-size: 0.9rem;
	}
	.footer-col a:hover {
		color: var(--color-brand);
	}
	.footer-bottom {
		margin-top: 44px;
		padding-top: 22px;
		border-top: 1px solid var(--color-border);
		display: flex;
		flex-direction: column;
		gap: 14px;
		align-items: center;
		justify-content: space-between;
		text-align: center;
	}
	@media (min-width: 640px) {
		.footer-bottom {
			flex-direction: row;
			text-align: left;
		}
	}
	.footer-bottom .copy {
		font-size: 0.82rem;
		color: var(--color-text-faint);
		font-family: var(--font-mono);
	}
	.footer-company {
		margin: 14px 0 0;
		font-size: 0.76rem;
		line-height: 1.5;
		color: var(--color-text-faint);
		text-align: center;
	}
	@media (min-width: 640px) {
		.footer-company {
			text-align: left;
		}
	}
	.footer-langs {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
	}
	.footer-langs button {
		background: none;
		border: 1px solid var(--color-border);
		border-radius: var(--r-sm);
		font-family: var(--font-mono);
		font-size: 0.72rem;
		font-weight: 600;
		color: var(--color-text-muted);
		padding: 5px 10px;
		cursor: pointer;
		transition: all 0.15s;
	}
	.footer-langs button:hover {
		border-color: var(--color-brand);
		color: var(--color-brand);
	}
	.footer-langs button.active {
		background: var(--color-brand);
		border-color: var(--color-brand);
		color: var(--color-brand-fg);
	}
</style>
