/**
 * Theme store using Svelte 5 Runes.
 *
 * The initial value is set inline in app.html before paint to avoid flash.
 * This module is for runtime toggling.
 */

import { browser } from '$app/environment';

type Theme = 'light' | 'dark';

function readInitial(): Theme {
	if (!browser) return 'dark';
	const stored = localStorage.getItem('theme') as Theme | null;
	if (stored === 'light' || stored === 'dark') return stored;
	return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

class ThemeStore {
	current = $state<Theme>(readInitial());

	toggle() {
		this.set(this.current === 'light' ? 'dark' : 'light');
	}

	set(theme: Theme) {
		this.current = theme;
		if (browser) {
			localStorage.setItem('theme', theme);
			document.documentElement.setAttribute('data-theme', theme);
		}
	}
}

export const theme = new ThemeStore();
