/**
 * Admin "active profile" scope store (Svelte 5 Runes).
 *
 * The multi-profile admin console can work in two modes:
 *   - GLOBAL  (`id === null`)  → every admin view shows data for ALL profiles
 *   - SCOPED  (`id === '<profile_id>'`) → views are filtered to one profile
 *
 * The selection is persisted to localStorage so it survives reloads. Admin
 * pages read `selectedProfile.id` into their query keys + `profile_id` query
 * params; switching profile re-runs the queries automatically.
 *
 * This is distinct from the per-user chat profile (which agent a normal user is
 * chatting with) — that lives on the chat page, not here.
 */

import { browser } from '$app/environment';

const STORAGE_KEY = 'admin:selectedProfile';

function readInitial(): string | null {
	if (!browser) return null;
	const stored = localStorage.getItem(STORAGE_KEY);
	return stored && stored !== '__global__' ? stored : null;
}

class SelectedProfileStore {
	/** The active profile id, or `null` for global (all profiles). */
	id = $state<string | null>(readInitial());

	set(id: string | null) {
		this.id = id;
		if (browser) {
			if (id) localStorage.setItem(STORAGE_KEY, id);
			else localStorage.setItem(STORAGE_KEY, '__global__');
		}
	}

	clear() {
		this.set(null);
	}
}

export const selectedProfile = new SelectedProfileStore();
