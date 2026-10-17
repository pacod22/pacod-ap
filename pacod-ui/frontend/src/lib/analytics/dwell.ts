/**
 * Visible-time accounting for the first-party page-view beacon.
 *
 * Pure (clock injected) so it can be unit-tested; the DOM wiring lives in
 * `PageViewTracker.svelte`. A "page view" starts on every client navigation;
 * dwell is the time the tab was actually VISIBLE, accumulated across
 * hide/show cycles and capped per view so a tab left open over a weekend
 * cannot report days of reading.
 *
 * Beacons are append-only: one `start` beacon (dwell 0) per view, then a
 * `delta` whenever the tab is hidden or the view ends. The server sums them.
 */

/** Per-view dwell ceiling (also enforced by the endpoint and the backend). */
export const MAX_DWELL_MS = 30 * 60 * 1000;
/** Deltas shorter than this are dropped to avoid beacon spam on tab flicker. */
export const MIN_DELTA_MS = 250;

export interface Beacon {
	pv: string;
	route: string;
	locale: string | null;
	dwell_ms: number;
	start: boolean;
	ref?: string | null;
}

interface View {
	pv: string;
	route: string;
	locale: string | null;
	visibleSince: number | null;
	accumulated: number;
}

export class DwellTracker {
	private view: View | null = null;

	constructor(
		private readonly now: () => number = () => performance.now(),
		private readonly newId: () => string = () => crypto.randomUUID()
	) {}

	get active(): boolean {
		return this.view !== null;
	}

	/** Begin a view. Returns the start beacon. */
	start(
		route: string,
		opts: { visible: boolean; locale?: string | null; ref?: string | null }
	): Beacon {
		this.view = {
			pv: this.newId(),
			route,
			locale: opts.locale ?? null,
			visibleSince: opts.visible ? this.now() : null,
			accumulated: 0
		};
		return {
			pv: this.view.pv,
			route,
			locale: this.view.locale,
			dwell_ms: 0,
			start: true,
			ref: opts.ref ?? null
		};
	}

	/** The tab became hidden: close the visible interval. */
	hidden(): Beacon | null {
		return this.collect(false);
	}

	/** The tab became visible again. */
	visible(): void {
		if (this.view && this.view.visibleSince === null) this.view.visibleSince = this.now();
	}

	/** The view ends (navigation or page hide). */
	end(): Beacon | null {
		const b = this.collect(false);
		this.view = null;
		return b;
	}

	private collect(keepVisible: boolean): Beacon | null {
		const v = this.view;
		if (!v || v.visibleSince === null) return null;
		const t = this.now();
		const raw = Math.max(0, t - v.visibleSince);
		v.visibleSince = keepVisible ? t : null;
		const delta = Math.min(Math.round(raw), MAX_DWELL_MS - v.accumulated);
		if (delta < MIN_DELTA_MS) return null;
		v.accumulated += delta;
		return { pv: v.pv, route: v.route, locale: v.locale, dwell_ms: delta, start: false };
	}
}

/** The referrer's host when it is another site; null for same-site / none. */
export function referrerHost(referrer: string, ownHost: string): string | null {
	if (!referrer) return null;
	try {
		const host = new URL(referrer).host.toLowerCase();
		return host && host !== ownHost.toLowerCase() ? host.slice(0, 128) : null;
	} catch {
		return null;
	}
}
