<script lang="ts">
	import { ChevronDown, Check } from 'lucide-svelte';
	import { getLocale, setLocale, type Locale } from '$lib/paraglide/runtime';
	import { m } from '$lib/paraglide/messages';
	import { page } from '$app/state';
	import { onlyEnabled } from '$lib/i18n/enabled-locales';

	// Native language names (conventional in language switchers — not translated).
	const LOCALES: { code: Locale; label: string }[] = [
		{ code: 'en', label: 'English' },
		{ code: 'bg', label: 'Български' },
		{ code: 'de', label: 'Deutsch' },
		{ code: 'ru', label: 'Русский' }
	];

	// Only the locales this deployment enables (root layout load, ENABLED_LOCALES).
	let offered = $derived(onlyEnabled(LOCALES, page.data.enabledLocales));
	let current = $derived(getLocale());
	let open = $state(false);
	let menuEl = $state<HTMLDivElement | null>(null);

	function pick(code: Locale) {
		open = false;
		if (code !== current) {
			// Paraglide persists the choice in the PARAGLIDE_LOCALE cookie and reloads
			// so the server re-renders every string in the new language (no flash).
			setLocale(code);
		}
	}

	function onWindowClick(e: MouseEvent) {
		if (open && menuEl && !menuEl.contains(e.target as Node)) open = false;
	}
	function onWindowKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') open = false;
	}
</script>

<svelte:window onclick={onWindowClick} onkeydown={onWindowKeydown} />

<!-- Small flag SVGs (viewBox 24x16). Inline → CSP-safe, no external assets. -->
{#snippet flag(code: Locale)}
	{#if code === 'en'}
		<!-- United Kingdom (Union Jack, simplified) -->
		<svg viewBox="0 0 24 16" class="h-3.5 w-5 rounded-[2px]" aria-hidden="true">
			<rect width="24" height="16" fill="#012169" />
			<path d="M0 0l24 16M24 0L0 16" stroke="#fff" stroke-width="3.2" />
			<path d="M0 0l24 16M24 0L0 16" stroke="#C8102E" stroke-width="1.9" />
			<path d="M12 0v16M0 8h24" stroke="#fff" stroke-width="5.3" />
			<path d="M12 0v16M0 8h24" stroke="#C8102E" stroke-width="3.2" />
		</svg>
	{:else if code === 'bg'}
		<!-- Bulgaria: white / green / red -->
		<svg viewBox="0 0 24 16" class="h-3.5 w-5 rounded-[2px]" aria-hidden="true">
			<rect width="24" height="16" fill="#fff" />
			<rect width="24" height="10.67" y="5.33" fill="#00966E" />
			<rect width="24" height="5.33" y="10.67" fill="#D62612" />
		</svg>
	{:else if code === 'de'}
		<!-- Germany: black / red / gold -->
		<svg viewBox="0 0 24 16" class="h-3.5 w-5 rounded-[2px]" aria-hidden="true">
			<rect width="24" height="16" fill="#000" />
			<rect width="24" height="10.67" y="5.33" fill="#DD0000" />
			<rect width="24" height="5.33" y="10.67" fill="#FFCE00" />
		</svg>
	{:else if code === 'ru'}
		<!-- Russia: white / blue / red -->
		<svg viewBox="0 0 24 16" class="h-3.5 w-5 rounded-[2px]" aria-hidden="true">
			<rect width="24" height="16" fill="#fff" />
			<rect width="24" height="10.67" y="5.33" fill="#0039A6" />
			<rect width="24" height="5.33" y="10.67" fill="#D52B1E" />
		</svg>
	{/if}
{/snippet}

<div class="relative" bind:this={menuEl}>
	<button
		class="flex items-center gap-1 rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
		onclick={() => (open = !open)}
		aria-haspopup="menu"
		aria-expanded={open}
		aria-label={m.language_switch()}
		title={m.language_switch()}
	>
		{@render flag(current)}
		<ChevronDown
			size={13}
			class={open ? 'rotate-180 transition-transform' : 'transition-transform'}
		/>
	</button>

	{#if open}
		<div
			role="menu"
			aria-label="Language"
			class="absolute right-0 z-40 mt-1 w-40 overflow-hidden rounded-lg border py-1 shadow-lg"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			{#each offered as loc (loc.code)}
				<button
					role="menuitemradio"
					aria-checked={loc.code === current}
					class="flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-xs transition-colors hover:bg-[var(--color-surface-3)]"
					style={loc.code === current ? 'color: var(--color-brand);' : 'color: var(--color-text);'}
					onclick={() => pick(loc.code)}
				>
					{@render flag(loc.code)}
					<span class="flex-1 truncate">{loc.label}</span>
					{#if loc.code === current}
						<Check size={13} class="shrink-0" />
					{/if}
				</button>
			{/each}
		</div>
	{/if}
</div>
