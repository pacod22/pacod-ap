<script lang="ts">
	/**
	 * First-party page-view + dwell-time measurement for the admin Analytics page.
	 *
	 * Runs ONLY while the visitor has accepted analytics cookies
	 * (`cookieConsent.current.analytics`, the same gate `Analytics.svelte` uses);
	 * the `/t/pv` endpoint re-checks the consent cookie server-side. Sets no
	 * cookie and stores nothing in the browser — identity is the existing session,
	 * attached by the server.
	 *
	 * Mounted once in the root `+layout.svelte`.
	 */
	import { afterNavigate } from '$app/navigation';
	import { page } from '$app/state';
	import { getLocale } from '$lib/paraglide/runtime';
	import { cookieConsent } from '$lib/stores/cookie-consent.svelte';
	import { DwellTracker, referrerHost, type Beacon } from './dwell';

	const tracker = new DwellTracker();
	let firstView = true;
	// Set by the first afterNavigate (which also fires for the initial page), so
	// the consent effect below never races it into a duplicate start beacon.
	let navigated = false;

	function enabled(): boolean {
		return cookieConsent.current.analytics === true;
	}

	function send(b: Beacon | null): void {
		if (!b || !enabled()) return;
		const body = JSON.stringify(b);
		try {
			// text/plain keeps sendBeacon a "simple" request (no preflight).
			if (navigator.sendBeacon?.('/t/pv', new Blob([body], { type: 'text/plain' }))) return;
		} catch {
			/* fall through */
		}
		void fetch('/t/pv', {
			method: 'POST',
			body,
			keepalive: true,
			headers: { 'content-type': 'application/json' }
		}).catch(() => {});
	}

	function startView(): void {
		const route = page.route.id;
		if (!route) return;
		send(
			tracker.start(route, {
				visible: document.visibilityState === 'visible',
				locale: getLocale(),
				ref: firstView ? referrerHost(document.referrer, location.host) : null
			})
		);
		firstView = false;
	}

	afterNavigate(() => {
		navigated = true;
		if (!enabled()) return;
		send(tracker.end());
		startView();
	});

	$effect(() => {
		if (!enabled()) {
			// Consent withdrawn (or never given): stop immediately, send nothing.
			tracker.end();
			return;
		}
		// Consent granted mid-page (banner accept): measure the current page too.
		if (navigated && !tracker.active) startView();

		const onVisibility = () => {
			if (document.visibilityState === 'hidden') send(tracker.hidden());
			else tracker.visible();
		};
		const onPageHide = () => send(tracker.end());
		document.addEventListener('visibilitychange', onVisibility);
		window.addEventListener('pagehide', onPageHide);
		return () => {
			document.removeEventListener('visibilitychange', onVisibility);
			window.removeEventListener('pagehide', onPageHide);
		};
	});
</script>
