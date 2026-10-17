<script lang="ts">
	// Non-blocking cookie-consent banner, mounted once in the root layout so it
	// appears across the whole site. It's a labelled `region` (not a modal): every
	// cookie we set today is essential or a saved preference, so there's no legal
	// basis to gate the app behind consent — and a non-blocking banner avoids the
	// focus-trap/dialog a11y burden.
	//
	// Two optional categories, kept separate because they are separate purposes
	// in law: "analytics" (audience measurement) and "marketing" (advertising and
	// remarketing pixels). Enforced via hasAnalyticsConsent() / hasMarketingConsent().
	import { untrack } from 'svelte';
	import { m } from '$lib/paraglide/messages';
	import { page } from '$app/state';
	import { Cookie } from 'lucide-svelte';
	import { cookieConsent, persistConsent } from '$lib/stores/cookie-consent.svelte';
	import type { ConsentState } from '$lib/consent';

	let { initial }: { initial: ConsentState } = $props();

	// The banner names the deployment's brand (SITE_NAME) and links its Cookie Policy.
	const brand = $derived(page.data.site?.siteName ?? 'PACOD');
	const policyHref = '/cookies';

	// Per-user state seeded from the server-read cookie (no flash on first paint).
	// `initial` is only ever a seed — once the user decides in-session we keep
	// that choice — so read it untracked rather than reacting to prop changes.
	let decided = $state(untrack(() => initial.decided));
	let analytics = $state(untrack(() => initial.analytics));
	let marketing = $state(untrack(() => initial.marketing));

	// Publish the server-seeded choice into the shared store so the tag loader
	// sees the right state on first paint, before the user touches anything.
	$effect(() => {
		untrack(() => {
			cookieConsent.current = {
				decided: initial.decided,
				analytics: initial.analytics,
				marketing: initial.marketing
			};
		});
	});
	// True while the user is looking at the granular toggles.
	let customizing = $state(false);

	// The region is present when the user hasn't decided yet, or has reopened the
	// preferences panel from elsewhere (e.g. the /cookies page).
	let visible = $derived(!decided || cookieConsent.settingsOpen);
	let showDetails = $derived(customizing || cookieConsent.settingsOpen);

	function commit(allowAnalytics: boolean, allowMarketing: boolean) {
		analytics = allowAnalytics;
		marketing = allowMarketing;
		persistConsent(allowAnalytics, allowMarketing);
		decided = true;
		customizing = false;
		cookieConsent.close();
	}

	function cancel() {
		// Reopened after a prior decision → just close without changing anything.
		customizing = false;
		cookieConsent.close();
	}
</script>

{#if visible}
	<section class="cookie-consent" class:details={showDetails} aria-label={m.cookie_consent_aria()}>
		<div class="cc-inner">
			<div class="cc-head">
				<span class="cc-icon" aria-hidden="true"><Cookie size={20} /></span>
				<div class="cc-copy">
					<p class="cc-title">{m.cookie_consent_title()}</p>
					<p class="cc-body">
						{m.cookie_consent_body({ brand })}
						<a href={policyHref}>{m.cookie_consent_learn_more()}</a>
					</p>
				</div>
			</div>

			{#if showDetails}
				<div class="cc-cats">
					<div class="cc-cat">
						<div class="cc-cat-text">
							<span class="cc-cat-name">{m.cookie_consent_cat_necessary()}</span>
							<span class="cc-cat-desc">{m.cookie_consent_cat_necessary_desc()}</span>
						</div>
						<span class="cc-always">{m.cookie_consent_always_on()}</span>
					</div>
					<label class="cc-cat cc-cat-toggle">
						<div class="cc-cat-text">
							<span class="cc-cat-name">{m.cookie_consent_cat_analytics()}</span>
							<span class="cc-cat-desc">{m.cookie_consent_cat_analytics_desc()}</span>
						</div>
						<input type="checkbox" bind:checked={analytics} class="cc-switch" />
					</label>
					<label class="cc-cat cc-cat-toggle">
						<div class="cc-cat-text">
							<span class="cc-cat-name">{m.cookie_consent_cat_marketing()}</span>
							<span class="cc-cat-desc">{m.cookie_consent_cat_marketing_desc()}</span>
						</div>
						<input type="checkbox" bind:checked={marketing} class="cc-switch" />
					</label>
				</div>
			{/if}

			<div class="cc-actions">
				{#if showDetails}
					<button type="button" class="cc-btn cc-ghost" onclick={cancel}>
						{m.cookie_consent_cancel()}
					</button>
					<button
						type="button"
						class="cc-btn cc-primary"
						onclick={() => commit(analytics, marketing)}
					>
						{m.cookie_consent_save()}
					</button>
				{:else}
					<button type="button" class="cc-btn cc-ghost" onclick={() => (customizing = true)}>
						{m.cookie_consent_customize()}
					</button>
					<button type="button" class="cc-btn cc-secondary" onclick={() => commit(false, false)}>
						{m.cookie_consent_necessary()}
					</button>
					<button type="button" class="cc-btn cc-primary" onclick={() => commit(true, true)}>
						{m.cookie_consent_accept()}
					</button>
				{/if}
			</div>
		</div>
	</section>
{/if}

<style>
	.cookie-consent {
		position: fixed;
		z-index: 200;
		left: 50%;
		bottom: 16px;
		transform: translateX(-50%);
		width: min(720px, calc(100vw - 24px));
		background: var(--color-surface);
		color: var(--color-text);
		border: 1px solid var(--color-border);
		border-radius: var(--r-lg, 16px);
		box-shadow: var(--shadow-lg, 0 12px 40px rgba(0, 0, 0, 0.22));
		animation: cc-in 0.35s cubic-bezier(0.16, 1, 0.3, 1);
	}
	@keyframes cc-in {
		from {
			opacity: 0;
			transform: translate(-50%, 16px);
		}
		to {
			opacity: 1;
			transform: translate(-50%, 0);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.cookie-consent {
			animation: none;
		}
	}

	.cc-inner {
		padding: 18px 20px;
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.cc-head {
		display: flex;
		gap: 13px;
		align-items: flex-start;
	}
	.cc-icon {
		display: grid;
		place-items: center;
		width: 36px;
		height: 36px;
		flex-shrink: 0;
		border-radius: 10px;
		background: var(--color-surface-2);
		color: var(--color-brand);
	}
	.cc-title {
		font-family: var(--font-display, inherit);
		font-weight: 700;
		font-size: 1rem;
		margin: 0 0 4px;
	}
	.cc-body {
		margin: 0;
		font-size: 0.88rem;
		line-height: 1.55;
		color: var(--color-text-muted);
	}
	.cc-body a {
		color: var(--color-brand);
		text-decoration: underline;
		text-underline-offset: 2px;
	}

	/* Granular category rows */
	.cc-cats {
		display: flex;
		flex-direction: column;
		gap: 10px;
		border-top: 1px solid var(--color-border);
		border-bottom: 1px solid var(--color-border);
		padding: 14px 0;
	}
	.cc-cat {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 14px;
	}
	.cc-cat-toggle {
		cursor: pointer;
	}
	.cc-cat-text {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.cc-cat-name {
		font-weight: 600;
		font-size: 0.9rem;
	}
	.cc-cat-desc {
		font-size: 0.8rem;
		color: var(--color-text-muted);
		line-height: 1.45;
	}
	.cc-always {
		font-family: var(--font-mono, inherit);
		font-size: 0.68rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--color-text-faint);
		flex-shrink: 0;
	}
	/* Minimal switch styling on a real checkbox (keeps it accessible + labelled) */
	.cc-switch {
		appearance: none;
		-webkit-appearance: none;
		width: 40px;
		height: 22px;
		border-radius: 999px;
		background: var(--color-border);
		position: relative;
		cursor: pointer;
		flex-shrink: 0;
		transition: background 0.18s;
	}
	.cc-switch::after {
		content: '';
		position: absolute;
		top: 2px;
		left: 2px;
		width: 18px;
		height: 18px;
		border-radius: 50%;
		background: #fff;
		transition: transform 0.18s;
	}
	.cc-switch:checked {
		background: var(--color-brand);
	}
	.cc-switch:checked::after {
		transform: translateX(18px);
	}
	.cc-switch:focus-visible {
		outline: 2px solid var(--color-brand);
		outline-offset: 2px;
	}

	.cc-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		justify-content: flex-end;
	}
	.cc-btn {
		font: inherit;
		font-size: 0.88rem;
		font-weight: 600;
		padding: 9px 16px;
		border-radius: var(--r-md, 10px);
		border: 1px solid transparent;
		cursor: pointer;
		transition:
			background 0.15s,
			border-color 0.15s,
			filter 0.15s;
	}
	.cc-ghost {
		background: transparent;
		border-color: var(--color-border);
		color: var(--color-text-muted);
	}
	.cc-ghost:hover {
		color: var(--color-text);
		border-color: var(--color-text-faint);
	}
	.cc-secondary {
		background: var(--color-surface-2);
		color: var(--color-text);
	}
	.cc-secondary:hover {
		filter: brightness(0.97);
	}
	.cc-primary {
		background: var(--color-brand);
		color: var(--color-brand-fg);
	}
	.cc-primary:hover {
		filter: brightness(1.06);
	}

	@media (max-width: 520px) {
		.cc-actions .cc-btn {
			flex: 1 1 auto;
		}
	}
</style>
