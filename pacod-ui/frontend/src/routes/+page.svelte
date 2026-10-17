<script lang="ts">
	import { AGENT_ICONS } from '$lib/marketing/agent-icons';
	// PACOD public landing page. Ported from the Claude Design "PACOD Landing Page"
	// onto the app's Fiscus tokens + Paraglide i18n. Theme-aware: colours map to
	// --color-* tokens (which flip with data-theme); brand showcase panels keep a
	// literal teal so their white text stays readable in dark mode.
	import { m } from '$lib/paraglide/messages';
	import { getLocale, setLocale, type Locale } from '$lib/paraglide/runtime';
	import { companyLine } from '$lib/legal/company';
	import LanguageSwitcher from '$lib/components/layout/LanguageSwitcher.svelte';
	import { theme } from '$lib/stores/theme.svelte';
	import { userLabel, userInitial, hasAnyRole } from '$lib/types/auth';
	import { Sun, Moon, ChevronDown, Settings, LogOut } from 'lucide-svelte';
	import Seo from '$lib/seo/Seo.svelte';
	import SocialLinks from '$lib/components/social/SocialLinks.svelte';
	import WhatsAppButton from '$lib/components/social/WhatsAppButton.svelte';
	import { trackConversion } from '$lib/analytics/track';
	import { onlyEnabled } from '$lib/i18n/enabled-locales';
	import { SIGN_OUT_PATH, signInHref } from '$lib/auth-links';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	// The signed-in user (or null) comes merged in from the root layout load.
	let user = $derived(data.user);
	// Admins/superusers get the console; everyone else the chat.
	let isAdmin = $derived(hasAnyRole(user, ['admin', 'superuser']));
	// Anonymous visitors are funnelled to pacod-id's sign-in; signed-in users go
	// straight into the app — admins to the dashboard, end users to their chat.
	const signIn = signInHref(undefined, { locale: getLocale() });
	const signUp = signInHref(undefined, { create: true, locale: getLocale() });
	let appHref = $derived(user ? (isAdmin ? '/dashboard' : '/chat') : signIn);
	// The sign-in is served by the server's pacod-id handle, not a page: the
	// client-side router must not try to render it.
	let appReload = $derived(user ? undefined : '');
	// Header shortcut label matches where appHref lands the signed-in user.
	let appLabel = $derived(isAdmin ? m.landing_nav_dashboard() : m.landing_nav_openchat());

	// --- Header shadow on scroll ---
	let scrolled = $state(false);
	function onScroll() {
		scrolled = window.scrollY > 8;
	}

	// --- Account menu (signed-in header dropdown) ---
	let userMenuOpen = $state(false);
	let userMenuEl = $state<HTMLDivElement | null>(null);
	function onWindowClick(e: MouseEvent) {
		if (userMenuOpen && userMenuEl && !userMenuEl.contains(e.target as Node)) {
			userMenuOpen = false;
		}
	}
	function onWindowKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') userMenuOpen = false;
	}

	// --- Reveal-on-scroll action (IntersectionObserver) ---
	function reveal(node: HTMLElement) {
		if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
			node.classList.add('in');
			return;
		}
		const io = new IntersectionObserver(
			(entries) => {
				for (const e of entries) {
					if (e.isIntersecting) {
						e.target.classList.add('in');
						io.unobserve(e.target);
					}
				}
			},
			{ threshold: 0.12, rootMargin: '0px 0px -40px 0px' }
		);
		io.observe(node);
		return { destroy: () => io.disconnect() };
	}

	// --- Newsletter (public, no-login subscribe → double opt-in) ---
	// POSTs to the public /api/v1/newsletter/subscribe endpoint (reached by
	// logged-out visitors — see PUBLIC_API_PREFIXES in hooks.server.ts). The
	// backend always replies generically and emails a confirmation link, so the
	// success copy tells the visitor to check their inbox.
	let email = $state('');
	let subscribed = $state(false);
	let subscribing = $state(false);
	let subError = $state(false);
	async function submitNewsletter(e: SubmitEvent) {
		e.preventDefault();
		const form = e.target as HTMLFormElement;
		const input = form.querySelector('input');
		if (!email || !(input as HTMLInputElement)?.checkValidity()) {
			input?.focus();
			return;
		}
		subscribing = true;
		subError = false;
		try {
			const res = await fetch('/api/v1/newsletter/subscribe', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ email })
			});
			if (!res.ok) throw new Error(String(res.status));
			subscribed = true;
			email = '';
			// Reports the signup to whichever ad platforms are configured AND
			// consented — a no-op otherwise, so no gate is needed at this call site.
			trackConversion('newsletter_subscribe', data.site.tags);
		} catch {
			subError = true;
		} finally {
			subscribing = false;
		}
	}

	// --- Footer language pills ---
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

	// Agent cards — one per released agent (profile). Accent colour per agent
	// (used for the icon tile + top rule). The built-in set below covers the
	// agents the platform ships: the `starter` demo agent and the optional tax
	// example.
	//
	// `slug` points at the agent's public landing page (`$lib/marketing/agents`).
	// Anonymous visitors and crawlers follow that; a signed-in user still goes
	// straight into the product via `appHref`, because sending someone who has
	// already signed up to a sales page is a regression.
	interface LandingCard {
		acc: string;
		soft: string;
		slug: string;
		icon: string;
		name: string;
		desc: string;
		tag: string | null;
	}
	// Built-in cards: used only when the backend's public agent list is
	// unreachable (`data.publicAgents === null`) — the list itself comes from the
	// released agents' published copy (Agent editor).
	const builtIn = (): LandingCard[] => [
		{
			acc: '#3f7cc4',
			soft: '#e6eef8',
			slug: 'starter',
			icon: 'chat',
			name: m.landing_agent1_name(),
			desc: m.landing_agent1_desc(),
			tag: m.landing_agent1_tag()
		},
		{
			acc: '#0e6e57',
			soft: '#e7f1ed',
			slug: 'tax',
			icon: 'ledger',
			name: m.landing_agent2_name(),
			desc: m.landing_agent2_desc(),
			tag: null
		}
	];
	const agents = $derived<LandingCard[]>(
		data.publicAgents
			? data.publicAgents.map((a) => {
					const c = a.card[current] ?? a.card.en ?? {};
					return {
						acc: a.accent || '#0e6e57',
						soft: a.soft || '#e7f1ed',
						slug: a.slug,
						icon: a.icon,
						name: c.name ?? a.slug,
						desc: c.desc ?? '',
						tag: c.tag ?? null
					};
				})
			: builtIn()
	);
	const agentHref = (slug: string) => (user ? appHref : `/agents/${slug}`);
</script>

<Seo
	title={m.landing_meta_title()}
	description={m.landing_meta_description()}
	canonicalPath="/"
	localized
	rawTitle
	ogImageAlt={m.landing_meta_title()}
/>

<svelte:window onscroll={onScroll} onclick={onWindowClick} onkeydown={onWindowKeydown} />

<!-- Day/night toggle — shared by the signed-in and anonymous header layouts. -->
{#snippet themeToggle()}
	<button
		type="button"
		class="icon-btn"
		onclick={() => theme.toggle()}
		aria-label={theme.current === 'dark' ? m.appshell_theme_to_light() : m.appshell_theme_to_dark()}
	>
		{#if theme.current === 'dark'}
			<Sun size={18} />
		{:else}
			<Moon size={18} />
		{/if}
	</button>
{/snippet}

<div class="landing">
	<!-- ============ HEADER ============ -->
	<header class="site-header" class:scrolled>
		<div class="header-inner container">
			<a href="#top" class="brand" aria-label="PACOD">
				<span class="brand-mark">P</span>
				<span class="brand-name">PACOD</span>
			</a>

			<nav class="nav-links" aria-label="Primary">
				<a href="#agents">{m.landing_nav_agents()}</a>
				<a href="#bestof">{m.landing_nav_bestof()}</a>
				<a href="#newsletter">{m.landing_nav_newsletter()}</a>
			</nav>

			<div class="header-actions">
				{#if user}
					<a href={appHref} class="btn btn-primary btn-sm">{appLabel}</a>
					<LanguageSwitcher />
					{@render themeToggle()}
					<!-- Account menu -->
					<div class="account" bind:this={userMenuEl}>
						<button
							type="button"
							class="account-trigger"
							onclick={() => (userMenuOpen = !userMenuOpen)}
							aria-haspopup="menu"
							aria-expanded={userMenuOpen}
							aria-label={m.appshell_account_menu()}
						>
							<span class="account-avatar" aria-hidden="true">{userInitial(user)}</span>
							<span class="account-name">{userLabel(user)}</span>
							<ChevronDown size={14} class={userMenuOpen ? 'chev open' : 'chev'} />
						</button>

						{#if userMenuOpen}
							<div class="account-menu" role="menu" aria-label={m.appshell_account()}>
								<div class="account-head">
									<div class="account-head-name">{userLabel(user)}</div>
									{#if user.email}<div class="account-head-email">{user.email}</div>{/if}
								</div>
								<div class="account-items">
									<a
										href="/account"
										role="menuitem"
										class="account-item"
										onclick={() => (userMenuOpen = false)}
									>
										<Settings size={15} />
										{m.appshell_account_settings()}
									</a>
									<form method="POST" action={SIGN_OUT_PATH}>
										<button type="submit" role="menuitem" class="account-item danger">
											<LogOut size={15} />
											{m.appshell_sign_out()}
										</button>
									</form>
								</div>
							</div>
						{/if}
					</div>
				{:else}
					<a href={signIn} data-sveltekit-reload class="btn btn-ghost">{m.landing_nav_signin()}</a>
					<LanguageSwitcher />
					{@render themeToggle()}
					<a href={signUp} data-sveltekit-reload class="btn btn-primary">{m.landing_nav_cta()}</a>
				{/if}
			</div>
		</div>
	</header>

	<main id="top">
		<!-- ============ HERO ============ -->
		<section class="hero">
			<div class="hero-bg"></div>
			<div class="container">
				<div class="hero-grid">
					<div class="hero-copy">
						<span class="eyebrow">{m.landing_hero_eyebrow()}</span>
						<h1>{m.landing_hero_title()}</h1>
						<p class="hero-sub">{m.landing_hero_sub()}</p>
						<div class="hero-cta">
							<a href={appHref} data-sveltekit-reload={appReload} class="btn btn-primary btn-lg"
								>{m.landing_hero_cta1()}</a
							>
							<a href="#agents" class="btn btn-outline btn-lg">{m.landing_hero_cta2()}</a>
						</div>
						<p class="hero-trust">
							<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
								><path d="M9 12l2 2 4-4" /><path
									d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z"
								/></svg
							>
							<span>{m.landing_hero_trust()}</span>
						</p>
					</div>

					<!-- App mock -->
					<div class="mock" aria-hidden="true">
						<div class="mock-bar">
							<div class="mock-dots"><i></i><i></i><i></i></div>
							<span class="mock-title">{m.landing_mock_title()}</span>
							<span class="mock-live"><i></i><span>{m.landing_mock_live()}</span></span>
						</div>
						<div class="mock-body">
							<aside class="mock-side">
								<div class="mock-side-label">{m.landing_mock_agents()}</div>
								<div class="mock-agent active">
									<span class="ico" style="background:#3f7cc4"></span><span
										>{m.landing_agent1_name()}</span
									>
								</div>
								<div class="mock-agent">
									<span class="ico" style="background:#0e6e57"></span><span
										>{m.landing_agent2_name()}</span
									>
								</div>
								<div class="mock-agent">
									<span class="ico" style="background:#bf9540"></span><span
										>{m.landing_agent3_name()}</span
									>
								</div>
							</aside>
							<div class="mock-chat">
								<div class="bubble q">{m.landing_mock_q()}</div>
								<div class="bubble a">
									<span>{m.landing_mock_a()}</span>
									<span class="src">{m.landing_mock_src()}</span>
								</div>
								<div class="mock-typing"><i></i><i></i><i></i></div>
							</div>
						</div>
						<div class="mock-badge">
							<span class="bnum">{m.landing_mock_badge_num()}</span>
							<span class="blbl">{m.landing_mock_badge_lbl()}</span>
						</div>
					</div>
				</div>
			</div>
		</section>

		<!-- ============ STATS ============ -->
		<section class="stats">
			<div class="container">
				<div class="stats-inner">
					<div class="stat">
						<div class="num figure">{m.landing_stat1_num()}</div>
						<div class="lbl">{m.landing_stat1_lbl()}</div>
					</div>
					<div class="stat">
						<div class="num figure">{m.landing_stat2_num()}</div>
						<div class="lbl">{m.landing_stat2_lbl()}</div>
					</div>
					<div class="stat">
						<div class="num figure">{m.landing_stat3_num()}</div>
						<div class="lbl">{m.landing_stat3_lbl()}</div>
					</div>
				</div>
			</div>
		</section>

		<!-- ============ AGENTS ============ -->
		<section class="section" id="agents">
			<div class="container">
				<div class="section-head" use:reveal>
					<span class="eyebrow">{m.landing_agents_eyebrow()}</span>
					<h2>{m.landing_agents_title()}</h2>
					<p>{m.landing_agents_sub()}</p>
				</div>

				<div class="agents-grid">
					{#each agents as agent (agent.slug)}
						<a
							href={agentHref(agent.slug)}
							class="agent-card"
							use:reveal
							style="--acc:{agent.acc};--acc-soft:{agent.soft}"
						>
							{#if agent.tag}<span class="agent-tag">{agent.tag}</span>{/if}
							<div class="agent-ico">
								<!-- static icon paths from AGENT_ICONS -->
								<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
									<!-- eslint-disable-next-line svelte/no-at-html-tags -->
									{@html AGENT_ICONS[agent.icon] ?? AGENT_ICONS.chat}
								</svg>
							</div>
							<h3>{agent.name}</h3>
							<p>{agent.desc}</p>
							<span class="agent-open"
								><span>{m.landing_agent_open()}</span><svg
									viewBox="0 0 24 24"
									fill="none"
									stroke="currentColor"
									stroke-width="2.2"><path d="M5 12h14M13 6l6 6-6 6" /></svg
								></span
							>
						</a>
					{/each}
				</div>
			</div>
		</section>

		<!-- ============ BEST OF ============ -->
		<section class="section bestof" id="bestof">
			<div class="container">
				<div class="section-head" use:reveal>
					<span class="eyebrow">{m.landing_bestof_eyebrow()}</span>
					<h2>{m.landing_bestof_title()}</h2>
				</div>

				<div class="bestof-grid">
					<div class="bestof-feature" use:reveal>
						<span class="kicker">{m.landing_bestof_kicker()}</span>
						<h3>{m.landing_bestof_feature_name()}</h3>
						<p>{m.landing_bestof_feature_desc()}</p>
						<a href={appHref} data-sveltekit-reload={appReload} class="btn btn-gold"
							>{m.landing_bestof_feature_cta()}</a
						>
						<div class="bestof-metrics">
							<div class="m">
								<div class="mn figure">{m.landing_bestof_m1_num()}</div>
								<div class="ml">{m.landing_bestof_m1_lbl()}</div>
							</div>
							<div class="m">
								<div class="mn figure">{m.landing_bestof_m2_num()}</div>
								<div class="ml">{m.landing_bestof_m2_lbl()}</div>
							</div>
							<div class="m">
								<div class="mn figure">{m.landing_bestof_m3_num()}</div>
								<div class="ml">{m.landing_bestof_m3_lbl()}</div>
							</div>
						</div>
					</div>

					<div class="bestof-side">
						<div class="asked" use:reveal>
							<h4>{m.landing_asked_title()}</h4>
							<ul>
								<li>
									<span class="qmark">?</span><span
										><span class="q">{m.landing_asked_q1()}</span><span class="via"
											>{m.landing_asked_via1()}</span
										></span
									>
								</li>
								<li>
									<span class="qmark">?</span><span
										><span class="q">{m.landing_asked_q2()}</span><span class="via"
											>{m.landing_asked_via2()}</span
										></span
									>
								</li>
								<li>
									<span class="qmark">?</span><span
										><span class="q">{m.landing_asked_q3()}</span><span class="via"
											>{m.landing_asked_via3()}</span
										></span
									>
								</li>
							</ul>
						</div>
						<div class="quote" use:reveal>
							<blockquote>{m.landing_quote_text()}</blockquote>
							<div class="by">{m.landing_quote_by()}</div>
						</div>
					</div>
				</div>
			</div>
		</section>

		<!-- ============ NEWSLETTER ============ -->
		<section class="section newsletter" id="newsletter">
			<div class="container">
				<div class="news-card" use:reveal>
					<span class="eyebrow">{m.landing_news_eyebrow()}</span>
					<h2>{m.landing_news_title()}</h2>
					<p>{m.landing_news_sub()}</p>
					<form class="news-form" onsubmit={submitNewsletter} novalidate>
						<input
							type="email"
							required
							bind:value={email}
							placeholder={m.landing_news_ph()}
							aria-label="Email"
							disabled={subscribing}
						/>
						<button type="submit" class="btn btn-gold" disabled={subscribing}
							>{m.landing_news_cta()}</button
						>
					</form>
					<p class="news-note">{m.landing_news_note()}</p>
					{#if subscribed}<p class="news-success show">{m.landing_news_success()}</p>{/if}
					{#if subError}<p class="news-error show">{m.landing_news_error()}</p>{/if}
				</div>
			</div>
		</section>

		<!-- ============ CLOSING CTA ============ -->
		<section class="section cta-band">
			<div class="container" use:reveal>
				<h2>{m.landing_cta_title()}</h2>
				<p>{m.landing_cta_sub()}</p>
				<a href={appHref} data-sveltekit-reload={appReload} class="btn btn-primary btn-lg"
					>{m.landing_cta_btn()}</a
				>
			</div>
		</section>
	</main>

	<!-- ============ FOOTER ============ -->
	<footer class="site-footer">
		<div class="container">
			<div class="footer-grid">
				<div class="footer-brand">
					<a href="#top" class="brand"
						><span class="brand-mark">P</span><span class="brand-name">PACOD</span></a
					>
					<p>{m.landing_footer_tagline()}</p>
					<div class="footer-social">
						<SocialLinks site={data.site} />
						<WhatsAppButton site={data.site} />
					</div>
				</div>
				<div class="footer-col">
					<h5>{m.landing_footer_col1()}</h5>
					<ul>
						<li><a href="#agents">{m.landing_nav_agents()}</a></li>
						<li><a href="#bestof">{m.landing_nav_bestof()}</a></li>
						<li><a href="#newsletter">{m.landing_nav_newsletter()}</a></li>
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
					{#each onlyEnabled(FOOTER_LANGS, data.enabledLocales) as loc (loc.code)}
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
	/* Scoped landing styles ported from the Claude Design landing page.
	   Local design tokens (radii, shadows, brand-deepest) live on .landing so
	   they don't disturb the app's global @theme; colours use --color-* tokens
	   so the page is theme-aware. */
	.landing {
		--r-sm: 9px;
		--r-md: 12px;
		--r-lg: 16px;
		--r-xl: 22px;
		--r-2xl: 28px;

		--shadow-sm: 0 1px 2px rgba(19, 33, 29, 0.04), 0 1px 3px rgba(19, 33, 29, 0.06);
		--shadow-md: 0 4px 12px rgba(19, 33, 29, 0.06), 0 2px 4px rgba(19, 33, 29, 0.04);
		--shadow-lg: 0 24px 48px -12px rgba(8, 56, 45, 0.18), 0 8px 20px -8px rgba(19, 33, 29, 0.1);
		--shadow-brand: 0 18px 40px -14px rgba(14, 110, 87, 0.45);

		/* Literal deep teal for brand showcase panels (theme-independent so their
		   white text is always readable). */
		--teal: #0e6e57;
		--teal-deep: #06342a;

		--maxw: 1160px;

		background: var(--color-surface);
		color: var(--color-text);
		font-size: 16px;
		line-height: 1.6;
		overflow-x: hidden;
	}

	.landing :global(*) {
		box-sizing: border-box;
	}

	.landing h1,
	.landing h2,
	.landing h3,
	.landing h4 {
		font-family: var(--font-display);
		font-weight: 800;
		letter-spacing: -0.025em;
		line-height: 1.08;
		margin: 0;
		color: var(--color-text);
		text-wrap: balance;
	}
	.landing p {
		margin: 0;
		text-wrap: pretty;
	}
	/* Base link colour — excludes .btn so button variants keep their own text colour. */
	.landing a:not(.btn) {
		color: var(--color-brand);
		text-decoration: none;
		transition: color 0.15s;
	}
	.landing a:not(.btn):hover {
		color: var(--color-brand-muted);
	}
	.landing svg {
		display: block;
		max-width: 100%;
	}

	/* ---------- Layout helpers ---------- */
	.container {
		width: 100%;
		max-width: var(--maxw);
		margin-inline: auto;
		padding-inline: 20px;
	}
	.section {
		padding-block: 72px;
	}
	@media (min-width: 900px) {
		.section {
			padding-block: 112px;
		}
	}

	.eyebrow {
		display: inline-flex;
		align-items: center;
		gap: 0.7rem;
		font-family: var(--font-mono);
		font-size: 0.72rem;
		font-weight: 600;
		letter-spacing: 0.2em;
		text-transform: uppercase;
		color: var(--color-brand);
	}
	.eyebrow::before {
		content: '';
		width: 26px;
		height: 1.5px;
		background: var(--color-gold);
	}

	.figure {
		font-family: var(--font-mono);
		font-feature-settings: 'tnum';
	}

	.section-head {
		max-width: 640px;
	}
	.section-head .eyebrow {
		margin-bottom: 18px;
	}
	.section-head h2 {
		font-size: clamp(1.9rem, 4.6vw, 3rem);
	}
	.section-head p {
		margin-top: 16px;
		font-size: clamp(1rem, 1.6vw, 1.15rem);
		color: var(--color-text-muted);
	}

	/* ---------- Buttons ---------- */
	.btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 0.5rem;
		font-family: var(--font-sans);
		font-weight: 600;
		font-size: 0.95rem;
		line-height: 1;
		padding: 0.85rem 1.4rem;
		border-radius: var(--r-md);
		border: 1px solid transparent;
		cursor: pointer;
		transition:
			transform 0.15s ease,
			box-shadow 0.2s ease,
			background 0.15s,
			color 0.15s,
			border-color 0.15s;
		white-space: nowrap;
		text-decoration: none;
	}
	.btn:active {
		transform: translateY(1px);
	}

	.btn-primary {
		background: var(--color-brand);
		color: var(--color-brand-fg);
		box-shadow: var(--shadow-brand);
	}
	.btn-primary:hover {
		background: var(--color-brand-muted);
		color: var(--color-brand-fg);
		box-shadow: 0 22px 46px -14px rgba(14, 110, 87, 0.55);
	}

	.btn-outline {
		background: var(--color-surface-2);
		color: var(--color-text);
		border-color: var(--color-border-strong);
		box-shadow: var(--shadow-sm);
	}
	.btn-outline:hover {
		border-color: var(--color-brand);
		color: var(--color-brand);
	}

	.btn-ghost {
		background: transparent;
		color: var(--color-text);
	}
	.btn-ghost:hover {
		color: var(--color-brand);
	}

	.btn-gold {
		background: var(--color-gold);
		color: #2a2107;
	}
	.btn-gold:hover {
		background: #ad8536;
	}

	.btn-lg {
		padding: 1.05rem 1.7rem;
		font-size: 1.02rem;
	}

	/* ---------- Header ---------- */
	.site-header {
		position: sticky;
		top: 0;
		z-index: 50;
		background: color-mix(in oklch, var(--color-surface) 82%, transparent);
		backdrop-filter: saturate(1.4) blur(14px);
		border-bottom: 1px solid transparent;
		transition:
			border-color 0.2s,
			background 0.2s;
	}
	.site-header.scrolled {
		border-bottom-color: var(--color-border);
		background: color-mix(in oklch, var(--color-surface) 90%, transparent);
	}
	.header-inner {
		display: flex;
		align-items: center;
		justify-content: space-between;
		height: 68px;
		gap: 16px;
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
		background: linear-gradient(150deg, var(--teal) 0%, var(--teal-deep) 100%);
		display: grid;
		place-items: center;
		color: #fff;
		font-family: var(--font-display);
		font-weight: 800;
		font-size: 1rem;
		box-shadow: var(--shadow-sm);
		position: relative;
	}
	.brand-mark::after {
		content: '';
		position: absolute;
		inset: 0;
		border-radius: 9px;
		box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.12);
	}

	.nav-links {
		display: none;
		align-items: center;
		gap: 4px;
	}
	@media (min-width: 900px) {
		.nav-links {
			display: flex;
		}
	}
	.nav-links a {
		color: var(--color-text-muted);
		font-weight: 500;
		font-size: 0.92rem;
		padding: 0.5rem 0.85rem;
		border-radius: var(--r-sm);
		transition:
			color 0.15s,
			background 0.15s;
	}
	.nav-links a:hover {
		color: var(--color-text);
		background: var(--color-surface-3);
	}

	.header-actions {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.header-actions .btn-ghost {
		display: none;
	}
	@media (min-width: 640px) {
		.header-actions .btn-ghost {
			display: inline-flex;
		}
	}

	/* ---------- Signed-in header: theme toggle + account menu ---------- */
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

	.account {
		position: relative;
	}
	.account-trigger {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 4px 8px 4px 4px;
		border: none;
		border-radius: 999px;
		background: transparent;
		color: var(--color-text);
		cursor: pointer;
		transition: background 0.15s;
	}
	.account-trigger:hover {
		background: var(--color-surface-2);
	}
	.account-avatar {
		display: grid;
		place-items: center;
		width: 32px;
		height: 32px;
		border-radius: 999px;
		background: var(--color-brand-soft);
		color: var(--color-brand);
		font-size: 0.85rem;
		font-weight: 600;
	}
	.account-name {
		display: none;
		font-size: 0.9rem;
		font-weight: 500;
		max-width: 12ch;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	@media (min-width: 640px) {
		.account-name {
			display: inline;
		}
	}
	.account-trigger :global(.chev) {
		color: var(--color-text-muted);
		transition: transform 0.15s;
	}
	.account-trigger :global(.chev.open) {
		transform: rotate(180deg);
	}

	.account-menu {
		position: absolute;
		right: 0;
		top: calc(100% + 8px);
		z-index: 60;
		width: 224px;
		overflow: hidden;
		border: 1px solid var(--color-border);
		border-radius: 12px;
		background: var(--color-surface);
		box-shadow: var(--shadow-md);
	}
	.account-head {
		padding: 10px 12px;
		border-bottom: 1px solid var(--color-border);
	}
	.account-head-name {
		font-size: 0.9rem;
		font-weight: 500;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.account-head-email {
		margin-top: 2px;
		font-size: 0.78rem;
		color: var(--color-text-muted);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.account-items {
		padding: 6px;
	}
	.account-items form {
		margin: 0;
	}
	.account-item {
		display: flex;
		width: 100%;
		align-items: center;
		gap: 10px;
		padding: 8px 10px;
		border: none;
		border-radius: 8px;
		background: transparent;
		color: var(--color-text);
		font-size: 0.88rem;
		text-align: left;
		cursor: pointer;
		transition:
			background 0.15s,
			color 0.15s;
	}
	/* Outrank `.landing a:not(.btn)` for the anchor menu item. */
	.landing a.account-item,
	.landing a.account-item:hover {
		color: var(--color-text);
	}
	.account-item:hover {
		background: var(--color-surface-2);
	}
	.account-item.danger {
		color: var(--color-danger);
	}
	.account-item.danger:hover {
		background: var(--color-danger-soft);
	}

	/* ---------- Hero ---------- */
	.hero {
		position: relative;
		padding-top: 40px;
		padding-bottom: 24px;
		overflow: hidden;
	}
	.hero-bg {
		position: absolute;
		inset: 0;
		z-index: 0;
		pointer-events: none;
		background:
			radial-gradient(760px 420px at 82% -8%, var(--color-brand-soft) 0%, transparent 60%),
			radial-gradient(620px 360px at 8% 8%, var(--color-gold-soft) 0%, transparent 55%);
		opacity: 0.7;
	}
	.hero .container {
		position: relative;
		z-index: 1;
	}
	.hero-grid {
		display: grid;
		grid-template-columns: 1fr;
		gap: 44px;
		align-items: center;
	}
	@media (min-width: 900px) {
		.hero-grid {
			grid-template-columns: 1.05fr 0.95fr;
			gap: 56px;
			padding-block: 40px;
		}
	}
	.hero-copy .eyebrow {
		margin-bottom: 22px;
	}
	.hero h1 {
		font-size: clamp(2.4rem, 6.4vw, 4rem);
		font-weight: 800;
	}
	.hero-sub {
		margin-top: 22px;
		font-size: clamp(1.05rem, 1.9vw, 1.28rem);
		color: var(--color-text-muted);
		max-width: 34ch;
	}
	.hero-cta {
		margin-top: 32px;
		display: flex;
		flex-wrap: wrap;
		gap: 12px;
	}
	.hero-trust {
		margin-top: 26px;
		display: flex;
		align-items: center;
		gap: 10px;
		font-size: 0.88rem;
		color: var(--color-text-faint);
		font-family: var(--font-mono);
		letter-spacing: 0.01em;
	}
	.hero-trust svg {
		width: 16px;
		height: 16px;
		color: var(--color-brand);
		flex-shrink: 0;
	}

	/* ---------- App mock ---------- */
	.mock {
		position: relative;
		background: var(--color-surface-2);
		border: 1px solid var(--color-border);
		border-radius: var(--r-2xl);
		box-shadow: var(--shadow-lg);
		overflow: hidden;
		max-width: 460px;
		margin-inline: auto;
	}
	@media (min-width: 900px) {
		.mock {
			max-width: none;
		}
	}
	.mock-bar {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 14px 18px;
		border-bottom: 1px solid var(--color-border);
		background: var(--color-surface);
	}
	.mock-dots {
		display: flex;
		gap: 6px;
	}
	.mock-dots i {
		width: 10px;
		height: 10px;
		border-radius: 50%;
		background: var(--color-border-strong);
	}
	.mock-bar .mock-title {
		font-family: var(--font-mono);
		font-size: 0.72rem;
		color: var(--color-text-faint);
		margin-left: 6px;
		letter-spacing: 0.04em;
	}
	.mock-live {
		margin-left: auto;
		display: inline-flex;
		align-items: center;
		gap: 6px;
		font-family: var(--font-mono);
		font-size: 0.66rem;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--color-brand);
	}
	.mock-live i {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: var(--color-brand);
		box-shadow: 0 0 0 0 rgba(14, 110, 87, 0.5);
		animation: pulse 2s infinite;
	}
	@keyframes pulse {
		0% {
			box-shadow: 0 0 0 0 rgba(14, 110, 87, 0.45);
		}
		70% {
			box-shadow: 0 0 0 7px rgba(14, 110, 87, 0);
		}
		100% {
			box-shadow: 0 0 0 0 rgba(14, 110, 87, 0);
		}
	}

	.mock-body {
		display: grid;
		grid-template-columns: 132px 1fr;
		min-height: 340px;
	}
	@media (max-width: 460px) {
		.mock-body {
			grid-template-columns: 104px 1fr;
		}
	}
	.mock-side {
		border-right: 1px solid var(--color-border);
		background: var(--color-surface);
		padding: 12px 10px;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.mock-side-label {
		font-family: var(--font-mono);
		font-size: 0.6rem;
		text-transform: uppercase;
		letter-spacing: 0.12em;
		color: var(--color-text-faint);
		padding: 6px 8px 4px;
	}
	.mock-agent {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 8px;
		border-radius: var(--r-sm);
		font-size: 0.78rem;
		font-weight: 500;
		color: var(--color-text-muted);
	}
	.mock-agent .ico {
		width: 9px;
		height: 9px;
		border-radius: 3px;
		flex-shrink: 0;
	}
	.mock-agent.active {
		background: var(--color-brand-soft);
		color: var(--color-brand-muted);
		font-weight: 600;
	}

	.mock-chat {
		padding: 18px 18px 20px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.bubble {
		max-width: 88%;
		padding: 11px 14px;
		border-radius: 14px;
		font-size: 0.82rem;
		line-height: 1.5;
	}
	.bubble.q {
		align-self: flex-end;
		background: var(--teal);
		color: #fff;
		border-bottom-right-radius: 5px;
	}
	.bubble.a {
		align-self: flex-start;
		background: var(--color-surface-3);
		color: var(--color-text);
		border-bottom-left-radius: 5px;
	}
	.bubble.a .src {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		margin-top: 8px;
		font-family: var(--font-mono);
		font-size: 0.62rem;
		color: var(--color-brand);
		letter-spacing: 0.03em;
	}
	.bubble.a .src::before {
		content: '◆';
		font-size: 0.55rem;
		color: var(--color-gold);
	}
	.mock-typing {
		align-self: flex-start;
		display: flex;
		gap: 4px;
		padding: 12px 14px;
		background: var(--color-surface-3);
		border-radius: 14px;
		border-bottom-left-radius: 5px;
	}
	.mock-typing i {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: var(--color-text-faint);
		animation: blink 1.4s infinite;
	}
	.mock-typing i:nth-child(2) {
		animation-delay: 0.2s;
	}
	.mock-typing i:nth-child(3) {
		animation-delay: 0.4s;
	}
	@keyframes blink {
		0%,
		60%,
		100% {
			opacity: 0.3;
		}
		30% {
			opacity: 1;
		}
	}

	.mock-badge {
		position: absolute;
		right: -14px;
		bottom: 26px;
		background: var(--color-surface-2);
		border: 1px solid var(--color-border);
		border-radius: var(--r-md);
		box-shadow: var(--shadow-md);
		padding: 10px 13px;
		display: flex;
		align-items: center;
		gap: 10px;
	}
	@media (max-width: 480px) {
		.mock-badge {
			display: none;
		}
	}
	.mock-badge .bnum {
		font-family: var(--font-display);
		font-weight: 800;
		font-size: 1.2rem;
		color: var(--color-brand);
	}
	.mock-badge .blbl {
		font-size: 0.66rem;
		color: var(--color-text-muted);
		line-height: 1.3;
		max-width: 84px;
	}

	/* ---------- Stats strip ---------- */
	.stats {
		border-block: 1px solid var(--color-border);
		background: var(--color-surface-2);
	}
	.stats-inner {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 0;
	}
	.stat {
		padding: 26px 16px;
		text-align: center;
		border-left: 1px solid var(--color-border);
	}
	.stat:first-child {
		border-left: none;
	}
	.stat .num {
		font-family: var(--font-display);
		font-weight: 800;
		font-size: clamp(1.7rem, 3.6vw, 2.4rem);
		color: var(--color-brand);
		letter-spacing: -0.02em;
	}
	.stat .lbl {
		margin-top: 4px;
		font-size: 0.8rem;
		color: var(--color-text-muted);
		font-family: var(--font-mono);
		letter-spacing: 0.02em;
	}

	/* ---------- Agents grid ---------- */
	.agents-grid {
		margin-top: 48px;
		display: grid;
		grid-template-columns: 1fr;
		gap: 16px;
	}
	@media (min-width: 640px) {
		.agents-grid {
			grid-template-columns: repeat(2, 1fr);
		}
	}
	@media (min-width: 980px) {
		.agents-grid {
			grid-template-columns: repeat(3, 1fr);
		}
	}

	/* Card is an <a> — outrank `.landing a:not(.btn)` so body text stays neutral,
	   not the brand link colour, in both rest and hover states. */
	.landing a.agent-card,
	.landing a.agent-card:hover {
		color: inherit;
	}
	.agent-card {
		display: block;
		position: relative;
		background: var(--color-surface-2);
		border: 1px solid var(--color-border);
		border-radius: var(--r-lg);
		padding: 24px 22px 22px;
		box-shadow: var(--shadow-sm);
		transition:
			transform 0.2s ease,
			box-shadow 0.2s ease,
			border-color 0.2s;
		overflow: hidden;
	}
	.agent-card:hover {
		transform: translateY(-4px);
		box-shadow: var(--shadow-md);
		border-color: var(--color-border-strong);
	}
	.agent-card::before {
		content: '';
		position: absolute;
		top: 0;
		left: 0;
		right: 0;
		height: 3px;
		background: var(--acc, var(--color-brand));
		opacity: 0;
		transition: opacity 0.2s;
	}
	.agent-card:hover::before {
		opacity: 1;
	}
	.agent-ico {
		width: 46px;
		height: 46px;
		border-radius: 12px;
		display: grid;
		place-items: center;
		background: var(--acc-soft, var(--color-brand-soft));
		color: var(--acc, var(--color-brand));
		margin-bottom: 18px;
	}
	.agent-ico svg {
		width: 24px;
		height: 24px;
	}
	.agent-card h3 {
		font-size: 1.14rem;
		font-weight: 700;
	}
	.agent-card p {
		margin-top: 8px;
		font-size: 0.92rem;
		color: var(--color-text-muted);
	}
	.agent-tag {
		position: absolute;
		top: 18px;
		right: 18px;
		font-family: var(--font-mono);
		font-size: 0.6rem;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		font-weight: 600;
		color: var(--color-text);
		background: var(--color-gold-soft);
		border: 1px solid color-mix(in oklch, var(--color-gold) 40%, transparent);
		padding: 3px 8px;
		border-radius: 100px;
	}
	.agent-open {
		margin-top: 16px;
		display: inline-flex;
		align-items: center;
		gap: 6px;
		font-size: 0.84rem;
		font-weight: 600;
		color: var(--color-brand);
		font-family: var(--font-mono);
		letter-spacing: 0.02em;
	}
	.agent-open svg {
		width: 14px;
		height: 14px;
		transition: transform 0.2s;
	}
	.agent-card:hover .agent-open svg {
		transform: translateX(3px);
	}

	/* ---------- Best of ---------- */
	.bestof {
		background: var(--color-surface-2);
		border-block: 1px solid var(--color-border);
	}
	.bestof-grid {
		margin-top: 48px;
		display: grid;
		grid-template-columns: 1fr;
		gap: 20px;
	}
	@media (min-width: 900px) {
		.bestof-grid {
			grid-template-columns: 1.15fr 0.85fr;
		}
	}

	.bestof-feature {
		background: linear-gradient(155deg, var(--teal) 0%, var(--teal-deep) 100%);
		color: #fff;
		border-radius: var(--r-xl);
		padding: 34px 32px;
		position: relative;
		overflow: hidden;
		box-shadow: var(--shadow-brand);
		display: flex;
		flex-direction: column;
	}
	.bestof-feature::after {
		content: '';
		position: absolute;
		width: 320px;
		height: 320px;
		right: -80px;
		top: -120px;
		background: radial-gradient(circle, rgba(191, 149, 64, 0.28) 0%, transparent 68%);
		pointer-events: none;
	}
	.bestof-feature .kicker {
		font-family: var(--font-mono);
		font-size: 0.68rem;
		text-transform: uppercase;
		letter-spacing: 0.16em;
		color: var(--color-gold);
		display: inline-flex;
		align-items: center;
		gap: 8px;
	}
	.bestof-feature .kicker::before {
		content: '★';
	}
	.bestof-feature h3 {
		color: #fff;
		font-size: clamp(1.6rem, 3.5vw, 2.2rem);
		margin-top: 16px;
	}
	.bestof-feature p {
		margin-top: 14px;
		color: rgba(255, 255, 255, 0.82);
		font-size: 1rem;
		max-width: 42ch;
	}
	.bestof-feature .btn {
		margin-top: 26px;
		align-self: flex-start;
	}
	.bestof-metrics {
		margin-top: 28px;
		display: flex;
		gap: 30px;
		flex-wrap: wrap;
		position: relative;
		z-index: 1;
	}
	.bestof-metrics .m .mn {
		font-family: var(--font-display);
		font-weight: 800;
		font-size: 1.6rem;
	}
	.bestof-metrics .m .ml {
		font-size: 0.74rem;
		color: rgba(255, 255, 255, 0.62);
		font-family: var(--font-mono);
		letter-spacing: 0.04em;
		margin-top: 2px;
	}

	.bestof-side {
		display: flex;
		flex-direction: column;
		gap: 20px;
	}
	.asked {
		background: var(--color-surface);
		border: 1px solid var(--color-border);
		border-radius: var(--r-xl);
		padding: 24px 24px 12px;
		flex: 1;
	}
	.asked h4 {
		font-family: var(--font-mono);
		font-size: 0.68rem;
		text-transform: uppercase;
		letter-spacing: 0.12em;
		color: var(--color-text-faint);
		font-weight: 600;
	}
	.asked ul {
		list-style: none;
		margin: 14px 0 0;
		padding: 0;
	}
	.asked li {
		display: flex;
		align-items: flex-start;
		gap: 12px;
		padding: 13px 0;
		border-top: 1px solid var(--color-border);
	}
	.asked li:first-child {
		border-top: none;
	}
	.asked .q {
		font-size: 0.9rem;
		font-weight: 500;
		color: var(--color-text);
		line-height: 1.4;
	}
	.asked .via {
		display: block;
		font-family: var(--font-mono);
		font-size: 0.66rem;
		color: var(--color-text-faint);
		margin-top: 3px;
		letter-spacing: 0.02em;
	}
	.asked .qmark {
		flex-shrink: 0;
		width: 22px;
		height: 22px;
		border-radius: 6px;
		background: var(--color-brand-soft);
		color: var(--color-brand);
		display: grid;
		place-items: center;
		font-family: var(--font-mono);
		font-size: 0.7rem;
		font-weight: 700;
		margin-top: 1px;
	}

	.quote {
		background: var(--color-gold-soft);
		border: 1px solid color-mix(in oklch, var(--color-gold) 30%, transparent);
		border-radius: var(--r-xl);
		padding: 24px;
	}
	.quote blockquote {
		margin: 0;
		font-family: var(--font-display);
		font-weight: 600;
		font-size: 1.05rem;
		line-height: 1.45;
		color: var(--color-text);
		letter-spacing: -0.01em;
	}
	.quote blockquote::before {
		content: '“';
		color: var(--color-gold);
		font-size: 1.4em;
		line-height: 0;
		vertical-align: -0.2em;
		margin-right: 2px;
	}
	.quote .by {
		margin-top: 14px;
		font-size: 0.82rem;
		color: var(--color-text-muted);
		font-family: var(--font-mono);
	}

	/* ---------- Newsletter ---------- */
	.news-card {
		background: linear-gradient(160deg, var(--teal-deep) 0%, var(--teal) 100%);
		border-radius: var(--r-2xl);
		padding: 48px 28px;
		text-align: center;
		color: #fff;
		position: relative;
		overflow: hidden;
		box-shadow: var(--shadow-lg);
	}
	@media (min-width: 760px) {
		.news-card {
			padding: 60px 48px;
		}
	}
	.news-card::before {
		content: '';
		position: absolute;
		inset: 0;
		background:
			radial-gradient(500px 240px at 15% 0%, rgba(191, 149, 64, 0.22) 0%, transparent 60%),
			radial-gradient(460px 260px at 100% 120%, rgba(255, 255, 255, 0.1) 0%, transparent 60%);
		pointer-events: none;
	}
	.news-card > :global(*) {
		position: relative;
		z-index: 1;
	}
	.news-card .eyebrow {
		color: var(--color-gold);
		justify-content: center;
	}
	.news-card .eyebrow::before {
		background: var(--color-gold);
	}
	.news-card h2 {
		color: #fff;
		font-size: clamp(1.7rem, 4vw, 2.6rem);
		margin-top: 16px;
	}
	.news-card p {
		margin: 16px auto 0;
		color: rgba(255, 255, 255, 0.8);
		max-width: 46ch;
	}
	.news-form {
		margin: 30px auto 0;
		display: flex;
		gap: 10px;
		max-width: 460px;
		flex-wrap: wrap;
	}
	.news-form input {
		flex: 1 1 220px;
		height: 52px;
		border-radius: var(--r-md);
		border: 1px solid rgba(255, 255, 255, 0.24);
		background: rgba(255, 255, 255, 0.1);
		color: #fff;
		padding: 0 16px;
		font-size: 0.95rem;
		font-family: var(--font-sans);
		outline: none;
		transition:
			border-color 0.15s,
			background 0.15s;
	}
	.news-form input::placeholder {
		color: rgba(255, 255, 255, 0.55);
	}
	.news-form input:focus {
		border-color: var(--color-gold);
		background: rgba(255, 255, 255, 0.16);
	}
	.news-form .btn {
		height: 52px;
		flex: 0 0 auto;
	}
	.news-note {
		margin-top: 14px;
		font-size: 0.78rem;
		color: rgba(255, 255, 255, 0.6);
		font-family: var(--font-mono);
	}
	.news-success {
		margin-top: 18px;
		color: var(--color-gold);
		font-weight: 600;
	}
	.news-error {
		margin-top: 18px;
		color: #fecaca;
		font-weight: 600;
	}
	.news-form input:disabled,
	.news-form .btn:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	/* ---------- Closing CTA ---------- */
	.cta-band {
		text-align: center;
	}
	.cta-band h2 {
		font-size: clamp(2rem, 5vw, 3.2rem);
	}
	.cta-band p {
		margin: 16px auto 0;
		color: var(--color-text-muted);
		font-size: 1.1rem;
		max-width: 40ch;
	}
	.cta-band .btn {
		margin-top: 30px;
	}

	/* ---------- Footer ---------- */
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

	/* ---------- Reveal on scroll ---------- */
	.section-head,
	.agent-card,
	.bestof-feature,
	.asked,
	.quote,
	.news-card,
	.cta-band .container {
		opacity: 0;
		transform: translateY(22px);
		transition:
			opacity 0.6s ease,
			transform 0.6s cubic-bezier(0.16, 1, 0.3, 1);
	}
	:global(.in) {
		opacity: 1 !important;
		transform: none !important;
	}
	@media (prefers-reduced-motion: reduce) {
		.section-head,
		.agent-card,
		.bestof-feature,
		.asked,
		.quote,
		.news-card,
		.cta-band .container {
			opacity: 1;
			transform: none;
			transition: none;
		}
		.mock-live i,
		.mock-typing i {
			animation: none;
		}
	}

	/* mobile nav trim */
	@media (max-width: 380px) {
		.brand span.brand-name {
			display: none;
		}
	}
</style>
