<script lang="ts">
	import { DEFAULT_PROFILE_ID } from '$lib/features/profiles/queries';
	import { browser } from '$app/environment';
	import { Plus, Trash2, Pencil, Layers } from 'lucide-svelte';
	import { hasRole } from '$lib/types/auth';
	import Button from '$lib/components/ui/Button.svelte';
	import {
		profilesQuery,
		createProfileMutation,
		updateProfileMutation,
		deleteProfileMutation,
		shortNameFor,
		type Profile,
		type ProfileCreateInput
	} from '$lib/features/profiles/queries';
	import { m } from '$lib/paraglide/messages';
	import { getLocale } from '$lib/paraglide/runtime';

	// Native language names for the per-locale short-name inputs (endonyms — not
	// translated, matching the language switcher).
	const SHORT_NAME_LOCALES = [
		{ code: 'en', label: 'English' },
		{ code: 'bg', label: 'Български' },
		{ code: 'de', label: 'Deutsch' },
		{ code: 'ru', label: 'Русский' }
	] as const;

	let { data } = $props();

	const canEdit = $derived(hasRole(data.user, 'superuser'));

	const query = profilesQuery();
	const createM = createProfileMutation();
	const updateM = updateProfileMutation();
	const deleteM = deleteProfileMutation();

	// SSR initial list until the browser query resolves.
	let profiles = $derived<Profile[]>(($query.data ?? data.initial) as Profile[]);

	// ---- Create / edit dialog -------------------------------------------
	let dialogOpen = $state(false);
	let editing = $state<Profile | null>(null);
	let form = $state({
		slug: '',
		name: '',
		short_name_en: '',
		short_name_bg: '',
		short_name_de: '',
		short_name_ru: '',
		description: '',
		topic_name: 'general',
		legal_year: 2026,
		languages: 'bg,en',
		is_active: true
	});
	let formError = $state<string | null>(null);

	function openCreate() {
		editing = null;
		form = {
			slug: '',
			name: '',
			short_name_en: '',
			short_name_bg: '',
			short_name_de: '',
			short_name_ru: '',
			description: '',
			topic_name: 'general',
			legal_year: 2026,
			languages: 'bg,en',
			is_active: true
		};
		formError = null;
		dialogOpen = true;
	}

	function openEdit(p: Profile) {
		editing = p;
		form = {
			slug: p.slug,
			name: p.name,
			short_name_en: p.short_name_en ?? '',
			short_name_bg: p.short_name_bg ?? '',
			short_name_de: p.short_name_de ?? '',
			short_name_ru: p.short_name_ru ?? '',
			description: p.description ?? '',
			topic_name: p.topic_name,
			legal_year: p.legal_year,
			languages: p.languages,
			is_active: p.is_active
		};
		formError = null;
		dialogOpen = true;
	}

	async function submit() {
		formError = null;
		try {
			if (editing) {
				await $updateM.mutateAsync({
					id: editing.id,
					patch: {
						name: form.name,
						short_name_en: form.short_name_en || null,
						short_name_bg: form.short_name_bg || null,
						short_name_de: form.short_name_de || null,
						short_name_ru: form.short_name_ru || null,
						description: form.description || null,
						topic_name: form.topic_name,
						legal_year: Number(form.legal_year),
						languages: form.languages,
						is_active: form.is_active
					}
				});
			} else {
				const input: ProfileCreateInput = {
					slug: form.slug,
					name: form.name,
					short_name_en: form.short_name_en || null,
					short_name_bg: form.short_name_bg || null,
					short_name_de: form.short_name_de || null,
					short_name_ru: form.short_name_ru || null,
					description: form.description || null,
					topic_name: form.topic_name,
					legal_year: Number(form.legal_year),
					languages: form.languages
				};
				await $createM.mutateAsync(input);
			}
			dialogOpen = false;
		} catch (e) {
			formError = (e as Error)?.message ?? m.profiles_save_failed();
		}
	}

	async function remove(p: Profile) {
		if (!browser) return;
		if (!confirm(m.profiles_delete_confirm({ name: p.name }))) {
			return;
		}
		try {
			await $deleteM.mutateAsync(p.id);
		} catch (e) {
			alert((e as Error)?.message ?? m.profiles_delete_failed());
		}
	}
</script>

<div class="mx-auto max-w-5xl">
	<div class="mb-4 flex items-center justify-between">
		<div class="flex items-center gap-2">
			<Layers size={20} style="color: var(--color-brand);" />
			<h1 class="text-lg font-semibold" style="color: var(--color-text);">
				{m.profiles_heading()}
			</h1>
		</div>
		{#if canEdit}
			<Button variant="primary" onclick={openCreate}>
				<Plus size={15} />
				{m.profiles_new()}
			</Button>
		{/if}
	</div>

	{#if !canEdit}
		<p class="mb-3 text-xs" style="color: var(--color-text-muted);">
			{m.profiles_readonly_note()}
		</p>
	{/if}

	<div class="overflow-hidden rounded-lg border" style="border-color: var(--color-border);">
		<table class="w-full text-sm">
			<thead>
				<tr style="background: var(--color-surface-2); color: var(--color-text-muted);">
					<th class="px-3 py-2 text-left font-medium">{m.profiles_col_name()}</th>
					<th class="px-3 py-2 text-left font-medium">{m.profiles_col_short_name()}</th>
					<th class="px-3 py-2 text-left font-medium">{m.profiles_col_slug()}</th>
					<th class="px-3 py-2 text-left font-medium">{m.profiles_col_year()}</th>
					<th class="px-3 py-2 text-left font-medium">{m.profiles_col_languages()}</th>
					<th class="px-3 py-2 text-left font-medium">{m.common_active_cap()}</th>
					{#if canEdit}<th class="px-3 py-2"></th>{/if}
				</tr>
			</thead>
			<tbody>
				{#each profiles as p (p.id)}
					<tr class="border-t" style="border-color: var(--color-border); color: var(--color-text);">
						<td class="px-3 py-2">
							<div class="font-medium">{p.name}</div>
							{#if p.description}
								<div class="text-xs" style="color: var(--color-text-muted);">{p.description}</div>
							{/if}
						</td>
						<td class="px-3 py-2 text-xs" style="color: var(--color-text-muted);"
							>{p.short_name_en || p.short_name_bg || p.short_name_de || p.short_name_ru
								? shortNameFor(p, getLocale())
								: '—'}</td
						>
						<td class="px-3 py-2 font-mono text-xs" style="color: var(--color-text-muted);"
							>{p.slug}</td
						>
						<td class="px-3 py-2">{p.legal_year}</td>
						<td class="px-3 py-2 text-xs">{p.languages}</td>
						<td class="px-3 py-2">
							{#if p.is_active}
								<span class="text-xs" style="color: var(--color-brand);"
									>{m.common_active_lc()}</span
								>
							{:else}
								<span class="text-xs" style="color: var(--color-text-faint);"
									>{m.common_inactive_lc()}</span
								>
							{/if}
						</td>
						{#if canEdit}
							<td class="px-3 py-2 text-right">
								<div class="flex justify-end gap-1">
									<button
										class="rounded-md p-1 transition-colors hover:bg-[var(--color-surface-3)]"
										title={m.common_edit()}
										onclick={() => openEdit(p)}
									>
										<Pencil size={14} />
									</button>
									{#if p.id !== DEFAULT_PROFILE_ID}
										<button
											class="rounded-md p-1 text-[var(--color-danger,#e5484d)] transition-colors hover:bg-[var(--color-surface-3)]"
											title={m.common_delete()}
											onclick={() => remove(p)}
										>
											<Trash2 size={14} />
										</button>
									{/if}
								</div>
							</td>
						{/if}
					</tr>
				{/each}
				{#if profiles.length === 0}
					<tr>
						<td
							colspan="7"
							class="px-3 py-8 text-center text-sm"
							style="color: var(--color-text-muted);"
						>
							{m.profiles_empty()}
						</td>
					</tr>
				{/if}
			</tbody>
		</table>
	</div>
</div>

{#if dialogOpen}
	<div
		class="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4"
		onclick={() => (dialogOpen = false)}
		role="presentation"
	>
		<div
			class="w-full max-w-md rounded-xl border p-5 shadow-xl"
			style="background: var(--color-surface); border-color: var(--color-border);"
			onclick={(e) => e.stopPropagation()}
			role="presentation"
		>
			<h2 class="mb-3 text-base font-semibold" style="color: var(--color-text);">
				{editing ? m.profiles_edit_title() : m.profiles_new_title()}
			</h2>
			<div class="space-y-3">
				{#if !editing}
					<label class="block text-xs" style="color: var(--color-text-muted);">
						{m.profiles_field_slug()}
						<input
							class="field-input mt-1 w-full"
							bind:value={form.slug}
							placeholder="bg-corporate"
						/>
					</label>
				{/if}
				<label class="block text-xs" style="color: var(--color-text-muted);">
					{m.profiles_field_name()}
					<input
						class="field-input mt-1 w-full"
						bind:value={form.name}
						placeholder="Customer Support Assistant"
					/>
				</label>
				<div class="space-y-1.5">
					<span class="block text-xs" style="color: var(--color-text-muted);">
						{m.profiles_field_short_names()}
					</span>
					{#each SHORT_NAME_LOCALES as loc (loc.code)}
						<label class="flex items-center gap-2 text-xs" style="color: var(--color-text-muted);">
							<span class="w-16 shrink-0">{loc.label}</span>
							<input
								class="field-input w-full"
								bind:value={form[`short_name_${loc.code}`]}
								placeholder="Corporate"
							/>
						</label>
					{/each}
				</div>
				<label class="block text-xs" style="color: var(--color-text-muted);">
					{m.profiles_field_description()}
					<input class="field-input mt-1 w-full" bind:value={form.description} />
				</label>
				<div class="flex gap-3">
					<label class="block flex-1 text-xs" style="color: var(--color-text-muted);">
						{m.profiles_field_legal_year()}
						<input type="number" class="field-input mt-1 w-full" bind:value={form.legal_year} />
					</label>
					<label class="block text-sm">
						{m.profiles_field_languages()}
						<input
							class="field-input mt-1 w-full"
							bind:value={form.languages}
							placeholder="bg,en"
						/>
					</label>
				</div>
				<label class="block text-xs" style="color: var(--color-text-muted);">
					{m.profiles_field_topic_name()}
					<input class="field-input mt-1 w-full" bind:value={form.topic_name} />
				</label>
				{#if editing}
					<label class="flex items-center gap-2 text-xs" style="color: var(--color-text-muted);">
						<input type="checkbox" bind:checked={form.is_active} />
						{m.common_active_cap()}
					</label>
				{/if}
				{#if formError}
					<p class="text-xs" style="color: var(--color-danger,#e5484d);">{formError}</p>
				{/if}
			</div>
			<div class="mt-4 flex justify-end gap-2">
				<Button variant="ghost" onclick={() => (dialogOpen = false)}>{m.common_cancel()}</Button>
				<Button
					variant="primary"
					onclick={submit}
					disabled={$createM.isPending || $updateM.isPending}
				>
					{editing ? m.common_save() : m.common_create()}
				</Button>
			</div>
		</div>
	</div>
{/if}
