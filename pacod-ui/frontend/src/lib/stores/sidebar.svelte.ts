/**
 * Sidebar collapse store using Svelte 5 Runes.
 *
 * Persists the desktop fold state across reloads. Mirrors the theme store.
 * Only affects the `lg:` breakpoint — on mobile the sidebar is a drawer.
 */

import { browser } from '$app/environment';

const KEY = 'sidebar:collapsed';

function readInitial(): boolean {
	if (!browser) return false;
	return localStorage.getItem(KEY) === '1';
}

class SidebarStore {
	collapsed = $state<boolean>(readInitial());

	toggle() {
		this.set(!this.collapsed);
	}

	set(value: boolean) {
		this.collapsed = value;
		if (browser) localStorage.setItem(KEY, value ? '1' : '0');
	}
}

export const sidebar = new SidebarStore();
