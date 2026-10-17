<script lang="ts">
	/**
	 * Range / site / profile filters shared by both analytics pages.
	 *
	 * The top-bar profile scope (`selectedProfile`) wins over the in-page profile
	 * select, exactly like the LLM Usage tab: when set, the select is replaced by
	 * a read-only indicator.
	 */
	import { m } from '$lib/paraglide/messages';
	import { X } from 'lucide-svelte';
	import { ANALYTICS_SITES } from './queries';
	import { RANGES, DEFAULT_RANGE, siteLabel } from './format';

	let {
		rangeId = $bindable(),
		site = $bindable(),
		profile = $bindable(),
		scopedProfileId,
		profileOptions,
		profileLabel,
		onchange
	}: {
		rangeId: string;
		site: string;
		profile: string;
		scopedProfileId: string | null;
		profileOptions: { id: string; label: string }[];
		profileLabel: (id: string) => string;
		onchange?: () => void;
	} = $props();

	const hasFilters = $derived(rangeId !== DEFAULT_RANGE || !!site || !!profile);

	function clear() {
		rangeId = DEFAULT_RANGE;
		site = '';
		profile = '';
		onchange?.();
	}
</script>

<div
	class="flex flex-wrap items-end gap-2 rounded-xl border p-2"
	style="border-color: var(--color-border); background: var(--color-surface-2);"
>
	<div>
		<label class="field-label" for="an-range">{m.analytics_filter_range()}</label>
		<select
			id="an-range"
			class="field-input h-10"
			bind:value={rangeId}
			onchange={() => onchange?.()}
		>
			{#each RANGES as r (r.id)}<option value={r.id}>{r.label()}</option>{/each}
		</select>
	</div>

	<div>
		<label class="field-label" for="an-site">{m.analytics_filter_site()}</label>
		<select id="an-site" class="field-input h-10" bind:value={site} onchange={() => onchange?.()}>
			<option value="">{m.analytics_filter_all_sites()}</option>
			{#each ANALYTICS_SITES as s (s)}
				<option value={s}>{siteLabel(s)}{siteLabel(s) === s ? '' : ` (${s})`}</option>
			{/each}
		</select>
	</div>

	<div>
		<label class="field-label" for="an-profile">{m.analytics_filter_profile()}</label>
		{#if scopedProfileId}
			<div
				id="an-profile"
				class="field-input flex h-10 items-center"
				style="opacity: 0.85;"
				title={m.analytics_filter_profile_scoped_hint()}
			>
				{profileLabel(scopedProfileId)}
			</div>
		{:else}
			<select
				id="an-profile"
				class="field-input h-10"
				bind:value={profile}
				onchange={() => onchange?.()}
			>
				<option value="">{m.analytics_filter_all_profiles()}</option>
				{#each profileOptions as p (p.id)}<option value={p.id}>{p.label}</option>{/each}
			</select>
		{/if}
	</div>

	{#if hasFilters}
		<button
			type="button"
			class="inline-flex h-10 items-center gap-1 rounded-lg px-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
			onclick={clear}
		>
			<X size={14} />
			{m.common_clear()}
		</button>
	{/if}
</div>
