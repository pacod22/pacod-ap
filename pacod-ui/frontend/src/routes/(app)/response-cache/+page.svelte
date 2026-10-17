<script lang="ts">
	import ManagedNotice from '$lib/components/agent-editor/ManagedNotice.svelte';
	import { untrack } from 'svelte';
	import { Zap, Save, Trash2, BarChart3 } from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import { m } from '$lib/paraglide/messages';
	import {
		createResponseCacheQuery,
		updateResponseCacheMutation,
		clearResponseCacheMutation,
		type ResponseCachePatch
	} from '$lib/features/response-cache/queries';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';

	let { data } = $props();

	const cfgQuery = $derived(createResponseCacheQuery(selectedProfile.id));
	const updateMut = $derived(updateResponseCacheMutation(selectedProfile.id));
	const clearMut = $derived(clearResponseCacheMutation(selectedProfile.id));

	// Effective values + stats from the live query, falling back to the SSR snapshot.
	const cfg = $derived($cfgQuery.data ?? data.initial);
	const stats = $derived(cfg.stats);

	// Editable form, seeded from the SSR snapshot (unscoped = default profile)
	// and re-seeded from the scoped query on a profile switch, since Save writes
	// to the selected profile.
	const eff = untrack(() => data.initial.effective); // seed once; re-seeded from the scoped query below
	let enabled = $state(eff.enabled);
	// Threshold is edited as a percentage for a friendlier control.
	let threshold_pct = $state(Math.round(eff.similarity_threshold * 100));
	let ttl_seconds = $state(eff.ttl_seconds);

	let saveError = $state('');
	let saved = $state(false);
	let clearMsg = $state('');

	let seededFor = $state<string | null | undefined>(null);
	$effect(() => {
		const pid = selectedProfile.id;
		const loaded = $cfgQuery.data?.effective;
		if (seededFor === pid) return;
		if (!loaded) {
			if (seededFor !== undefined) seededFor = undefined;
			return;
		}
		enabled = loaded.enabled;
		threshold_pct = Math.round(loaded.similarity_threshold * 100);
		ttl_seconds = loaded.ttl_seconds;
		seededFor = pid;
		saved = false;
		saveError = '';
	});

	async function saveConfig() {
		saveError = '';
		saved = false;
		const patch: ResponseCachePatch = {
			enabled,
			similarity_threshold: Math.min(1, Math.max(0, Number(threshold_pct) / 100)),
			ttl_seconds: Number(ttl_seconds)
		};
		try {
			await $updateMut.mutateAsync(patch);
			saved = true;
		} catch (e: unknown) {
			saveError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	async function clearCache() {
		clearMsg = '';
		try {
			const res = await $clearMut.mutateAsync();
			clearMsg = m.response_cache_cleared({ n: res.deleted_buckets });
		} catch (e: unknown) {
			clearMsg = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	function ttlLabel(s: number): string {
		if (s <= 0) return m.response_cache_ttl_no_expiry();
		if (s % 86400 === 0) return m.response_cache_ttl_days({ n: s / 86400 });
		if (s % 3600 === 0) return m.response_cache_ttl_hours({ n: s / 3600 });
		return m.response_cache_ttl_seconds({ n: s });
	}
</script>

<svelte:head><title>{m.response_cache_title()}</title></svelte:head>

<div class="space-y-8">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">{m.response_cache_heading()}</h1>
		<p class="mt-1 max-w-2xl text-sm text-[var(--color-text-muted)]">
			{m.response_cache_intro()}
		</p>
	</div>

	<ManagedNotice fields={cfg.managed_fields} />

	<!-- Configuration -->
	<section class="rounded-xl border p-5" style="border-color: var(--color-border);">
		<div class="mb-4 flex items-center gap-2">
			<Zap size={16} />
			<h2 class="text-base font-semibold">{m.common_configuration()}</h2>
		</div>

		<div class="space-y-4">
			<label class="flex items-center gap-2 text-sm">
				<input type="checkbox" bind:checked={enabled} />
				{m.response_cache_enable()}
			</label>

			<div class="grid grid-cols-1 gap-4 md:grid-cols-2">
				<div>
					<label class="field-label" for="fld-page-response-cache-1"
						>{m.response_cache_threshold_label({ pct: threshold_pct })}</label
					>
					<input
						id="fld-page-response-cache-1"
						type="range"
						min="50"
						max="100"
						step="1"
						bind:value={threshold_pct}
						class="w-full"
					/>
					<p class="mt-1 text-xs text-[var(--color-text-muted)]">
						{m.response_cache_threshold_hint()}
					</p>
				</div>
				<div>
					<label class="field-label" for="fld-page-response-cache-2"
						>{m.response_cache_ttl_label({ label: ttlLabel(Number(ttl_seconds)) })}</label
					>
					<input
						id="fld-page-response-cache-2"
						type="number"
						min="0"
						step="3600"
						bind:value={ttl_seconds}
						class="field-input"
					/>
					<p class="mt-1 text-xs text-[var(--color-text-muted)]">
						{m.response_cache_ttl_hint()}
					</p>
				</div>
			</div>
		</div>

		<div class="mt-5 flex items-center gap-3">
			<Button size="sm" onclick={saveConfig} disabled={$updateMut.isPending}>
				<Save size={14} />
				{m.common_save_configuration()}
			</Button>
			{#if saved}<span class="text-sm" style="color: var(--color-brand);">{m.common_saved()}</span
				>{/if}
			{#if saveError}<span class="text-sm" style="color: var(--color-danger);">{saveError}</span
				>{/if}
		</div>
	</section>

	<!-- Stats -->
	<section class="rounded-xl border p-5" style="border-color: var(--color-border);">
		<div class="mb-4 flex items-center gap-2">
			<BarChart3 size={16} />
			<h2 class="text-base font-semibold">{m.response_cache_statistics()}</h2>
		</div>
		<div class="grid grid-cols-2 gap-4 md:grid-cols-4">
			<div>
				<div class="text-2xl font-semibold">{stats.entries}</div>
				<div class="text-xs text-[var(--color-text-muted)]">{m.response_cache_entries()}</div>
			</div>
			<div>
				<div class="text-2xl font-semibold">{stats.hits}</div>
				<div class="text-xs text-[var(--color-text-muted)]">{m.response_cache_hits()}</div>
			</div>
			<div>
				<div class="text-2xl font-semibold">{stats.misses}</div>
				<div class="text-xs text-[var(--color-text-muted)]">{m.response_cache_misses()}</div>
			</div>
			<div>
				<div class="text-2xl font-semibold">{(stats.hit_rate * 100).toFixed(1)}%</div>
				<div class="text-xs text-[var(--color-text-muted)]">{m.response_cache_hit_rate()}</div>
			</div>
		</div>

		<div class="mt-5 flex items-center gap-3">
			<Button size="sm" variant="ghost" onclick={clearCache} disabled={$clearMut.isPending}>
				<Trash2 size={14} />
				{m.response_cache_clear()}
			</Button>
			{#if clearMsg}<span class="text-sm text-[var(--color-text-muted)]">{clearMsg}</span>{/if}
		</div>
		<p class="mt-2 text-xs text-[var(--color-text-muted)]">
			{m.response_cache_clear_hint()}
		</p>
	</section>
</div>
